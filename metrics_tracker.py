"""Viral Feedback Loop: periodically visits your own TikTok Studio content list (via
cookies.json, reusing tiktok_uploader.py's cookie auth) to read real view/like counts for
clips you've uploaded, and stores them in viral_memory.json.

train_loop.py's critic reads viral_memory.json before generating new rules (see
load_viral_memory_section() in train_loop.py) — what actually performs on TikTok feeds back
into future clip selection via analyze.py's injected guidelines, closing the loop:
    auto_pilot.py uploads a clip + writes a metadata sidecar (title, description, hashtags,
    caption, viral_score, energy_rating, reward_score) next to it in uploaded_clips/
      -> metrics_tracker.py matches that sidecar's caption against the live TikTok content
         list and records real views/likes into viral_memory.json
      -> train_loop.py's next critic pass reads viral_memory.json and generates
         POSITIVE/PENALTY "viral pattern" rules from what actually won or flopped
      -> analyze.py's next clip selection is shaped by those rules

Run this as its own long-lived process, independent of auto_pilot.py/orchestrator.py:
    python metrics_tracker.py
    python metrics_tracker.py --poll-interval 1800
    python metrics_tracker.py --once            # single pass, then exit

TikTok's Studio UI isn't a stable public contract and changes over time, so the scraping
selectors here are best-effort — spot-check with --headed if metrics stop updating. Never
raises out of its own loop: a broken scrape just skips that cycle instead of crashing.
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import atomic_io
import tiktok_uploader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

UPLOADED_CLIPS_DIR = Path("uploaded_clips")
VIRAL_MEMORY_PATH = Path("viral_memory.json")

# TikTok Studio's own content/analytics list — session-relative ("my content"), so no
# username needs to be configured. Same domain as tiktok_uploader.py's upload page.
CONTENT_URL = "https://www.tiktok.com/tiktokstudio/content"
NAV_TIMEOUT_MS = 60_000

# View/like counts move slowly; polling every few minutes would be pointless and closer to
# the kind of aggressive automation that gets accounts flagged. Default to every 30 minutes.
DEFAULT_POLL_INTERVAL_SECONDS = 1800

# Caption text may be truncated in TikTok's list view — match on a shared prefix instead of
# requiring an exact string match.
CAPTION_MATCH_PREFIX_LEN = 40


def load_uploaded_metadata() -> List[dict]:
    """Every *.json sidecar auto_pilot.py's Phase 5 (Deployment) writes next to an uploaded
    clip — see run_deployment_phase() in auto_pilot.py."""
    if not UPLOADED_CLIPS_DIR.exists():
        return []

    entries = []
    for sidecar_path in UPLOADED_CLIPS_DIR.glob("*.json"):
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read %s: %s", sidecar_path, e)
            continue
        entry["_clip_id"] = sidecar_path.stem
        entries.append(entry)
    return entries


def _parse_abbreviated_count(raw: str) -> Optional[int]:
    """TikTok abbreviates large counts (12.3K, 1.2M) in list views."""
    raw = raw.upper().replace(",", "").strip()
    try:
        if raw.endswith("K"):
            return int(float(raw[:-1]) * 1_000)
        if raw.endswith("M"):
            return int(float(raw[:-1]) * 1_000_000)
        return int(float(raw))
    except ValueError:
        return None


def _extract_count(text: str, patterns: List[str]) -> Optional[int]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            count = _parse_abbreviated_count(match.group(1))
            if count is not None:
                return count
    return None


# English + German (TikTok Studio locale depends on the account's language setting, not the
# machine running this script) plus emoji fallbacks that work regardless of locale.
VIEW_COUNT_PATTERNS = [
    r"([\d.,]+[KM]?)\s*views?", r"([\d.,]+[KM]?)\s*Aufrufe", r"👁\D*([\d.,]+[KM]?)",
]
LIKE_COUNT_PATTERNS = [
    r"([\d.,]+[KM]?)\s*likes?", r"([\d.,]+[KM]?)\s*(?:Gefällt mir|Likes)", r"❤\D*([\d.,]+[KM]?)",
]


def fetch_content_list(headless: bool = True) -> List[dict]:
    """Best-effort scrape of the TikTok Studio content list:
    [{"caption": str, "views": int | None, "likes": int | None}, ...]. Returns [] (never
    raises) on any failure — a broken scrape must degrade gracefully, not crash the loop."""
    cookies = tiktok_uploader.load_cookies()
    if not cookies:
        logger.error(
            "No usable cookies found in %s — cannot fetch TikTok metrics.", tiktok_uploader.COOKIES_PATH,
        )
        return []

    rows: List[dict] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.set_default_timeout(NAV_TIMEOUT_MS)
            context.add_cookies(cookies)
            page = context.new_page()

            try:
                page.goto(CONTENT_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)

                items = page.locator("[data-e2e='content-item']").all()
                if not items:
                    # Selector drift fallback — TikTok Studio's markup changes often.
                    items = page.locator("[class*='ContentItem'], [class*='content-item']").all()

                for item in items:
                    try:
                        text = item.text_content(timeout=3000) or ""
                    except PlaywrightTimeoutError:
                        continue
                    if not text.strip():
                        continue

                    try:
                        caption_el = item.locator(
                            "[data-e2e='content-item-caption'], [class*='caption'], [class*='Caption']"
                        ).first
                        caption = caption_el.text_content(timeout=2000)
                    except PlaywrightTimeoutError:
                        caption = text[:200]

                    rows.append({
                        "caption": (caption or "").strip(),
                        "views": _extract_count(text, VIEW_COUNT_PATTERNS),
                        "likes": _extract_count(text, LIKE_COUNT_PATTERNS),
                    })
            finally:
                context.close()
                browser.close()
    except Exception as e:
        logger.error("Could not fetch TikTok content list: %s", e)
        return []

    return rows


def _captions_match(sidecar_caption: str, scraped_caption: str) -> bool:
    a, b = sidecar_caption.strip(), scraped_caption.strip()
    if not a or not b:
        return False
    prefix_len = min(CAPTION_MATCH_PREFIX_LEN, len(a), len(b))
    return a[:prefix_len].lower() == b[:prefix_len].lower()


def load_viral_memory(path: Path = VIRAL_MEMORY_PATH) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s, starting fresh: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def save_viral_memory(memory: Dict[str, dict], path: Path = VIRAL_MEMORY_PATH) -> None:
    atomic_io.atomic_write_json(path, memory)
    logger.info("Saved %d entrie(s) to %s", len(memory), path)


def update_viral_memory(headless: bool = True) -> int:
    """One full pass: match every locally-uploaded clip against the live TikTok content
    list and update its view/like counts in viral_memory.json. Returns how many entries
    were matched and updated this pass."""
    uploaded = load_uploaded_metadata()
    if not uploaded:
        logger.info("No uploaded clips with metadata found in %s", UPLOADED_CLIPS_DIR)
        return 0

    content_rows = fetch_content_list(headless=headless)
    if not content_rows:
        logger.warning("Could not fetch any TikTok content rows this cycle — skipping update")
        return 0

    memory = load_viral_memory()
    matched = 0

    for entry in uploaded:
        clip_id = entry["_clip_id"]
        sidecar_caption = entry.get("caption", "")

        match = next((row for row in content_rows if _captions_match(sidecar_caption, row["caption"])), None)
        if match is None:
            continue

        memory[clip_id] = {
            **{k: v for k, v in entry.items() if k != "_clip_id"},
            "views": match["views"],
            "likes": match["likes"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        matched += 1

    if matched:
        save_viral_memory(memory)
    logger.info("Matched %d/%d uploaded clip(s) against TikTok's content list", matched, len(uploaded))
    return matched


def run_tracker(poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS, max_iterations: Optional[int] = None) -> None:
    logger.info("📈 Metrics Tracker gestartet (poll_interval=%ds)", poll_interval)
    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            try:
                update_viral_memory()
            except Exception as e:
                logger.error("Metrics-Update fehlgeschlagen: %s", e)

            if max_iterations is None or iteration < max_iterations:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Metrics Tracker durch Nutzer gestoppt.")


def main():
    parser = argparse.ArgumentParser(
        description="Periodically fetch TikTok view/like counts for uploaded clips into viral_memory.json."
    )
    parser.add_argument(
        "--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Seconds between metric refreshes (default: %(default)s)",
    )
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after N cycles (omit to run forever)")
    parser.add_argument("--headed", action="store_true", help="Show the browser window (for debugging selector drift)")
    parser.add_argument("--once", action="store_true", help="Run a single update pass and exit")
    args = parser.parse_args()

    if args.once:
        update_viral_memory(headless=not args.headed)
    else:
        run_tracker(args.poll_interval, args.max_iterations)


if __name__ == "__main__":
    main()
