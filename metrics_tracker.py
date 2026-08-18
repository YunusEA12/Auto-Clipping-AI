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
      -> once a clip has been matched on two SEPARATE poll cycles (not just one — see
         _delete_confirmed_upload()), its local .mp4/.json in uploaded_clips/ is permanently
         deleted to save disk space; its view/like history stays in viral_memory.json
      -> train_loop.py's next critic pass reads viral_memory.json and generates
         POSITIVE/PENALTY "viral pattern" rules from what actually won or flopped
      -> analyze.py's next clip selection is shaped by those rules

Run this as its own long-lived process, independent of auto_pilot.py/orchestrator.py:
    python metrics_tracker.py
    python metrics_tracker.py --poll-interval 1800
    python metrics_tracker.py --once            # single pass, then exit

TikTok's Studio UI isn't a stable public contract and changes over time. Verified against
the live UI on 2026-08-18 (see C-01 in the audit): the content list's real per-row DOM has
no "views"/"likes" *words* next to the numbers at all (only the column headers say that), so
a text-pattern regex over row text could never reliably work regardless of which CSS
selector picked the row. Instead, this extracts TikTok's own embedded item data — a JSON
blob it renders the page from — out of a <script type="application/json"
id="__Creator_Center_Context__"> tag, falling back to best-effort DOM scraping only if that
script tag itself goes missing (real drift). Spot-check with --headed (or re-run
verify_tiktok_selectors.py) if metrics stop updating. Never raises out of its own loop: a
broken scrape just skips that cycle instead of crashing.
"""

import argparse
import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import atomic_io
import tiktok_uploader

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

UPLOADED_CLIPS_DIR = Path("uploaded_clips")
VIRAL_MEMORY_PATH = Path("viral_memory.json")

# Tracks scrape health across cycles — distinguishes "nothing to check yet" (0 uploaded
# clips, a perfectly normal state) from "the scrape itself is broken" (uploaded clips exist,
# but fetch_content_list() has come back empty several cycles running, meaning every
# selector is failing to match — almost certainly TikTok Studio's markup drifted). Read by
# scrape_health() below; app.py's dashboard can surface it as a distinct alert state instead
# of silently looking identical to "no clips uploaded yet".
SCRAPE_HEALTH_PATH = Path("metrics_scrape_health.json")
# After this many consecutive empty scrapes (with uploaded clips actually present to match
# against), escalate from a routine warning to an error-level log line — loud enough that
# console/log-based monitoring actually notices instead of scrolling past "0 matched" forever.
CONSECUTIVE_FAILURE_ALERT_THRESHOLD = 3

# TikTok Studio's own content/analytics list — session-relative ("my content"), so no
# username needs to be configured. Same domain as tiktok_uploader.py's upload page.
CONTENT_URL = "https://www.tiktok.com/tiktokstudio/content"
NAV_TIMEOUT_MS = 60_000

# The <script type="application/json" id="..."> tag TikTok Studio's own frontend hydrates
# the content list from — confirmed present via a real content-list snapshot on 2026-08-18.
CONTEXT_SCRIPT_ID = "__Creator_Center_Context__"

# viral_memory.json is a pure accumulator with nothing pruning it — every clip ever checked
# stays in it forever, growing the block of text injected into every future critic prompt
# (see train_loop.load_viral_memory_section()) even for entries whose real-world relevance
# faded months ago. Entries older than this many days since their last check are dropped on
# each update pass.
VIRAL_MEMORY_MAX_AGE_DAYS = 180

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
# machine running this script) plus emoji fallbacks that work regardless of locale. Only
# used by the DOM-scraping fallback path below — the primary path (embedded JSON) doesn't
# need pattern matching at all, since play_count/like_count are already plain integers.
VIEW_COUNT_PATTERNS = [
    r"([\d.,]+[KM]?)\s*views?", r"([\d.,]+[KM]?)\s*Aufrufe", r"👁\D*([\d.,]+[KM]?)",
]
LIKE_COUNT_PATTERNS = [
    r"([\d.,]+[KM]?)\s*likes?", r"([\d.,]+[KM]?)\s*(?:Gefällt mir|Likes)", r"❤\D*([\d.,]+[KM]?)",
]


def _coerce_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_context_json(page) -> Optional[dict]:
    """Pulls TikTok Studio's own embedded item data out of the <script type="application/json"
    id="__Creator_Center_Context__"> tag its frontend hydrates the content list from. Returns
    None (never raises) if that tag isn't present or isn't valid JSON — real drift, not a
    routine empty-list case, which this distinguishes from downstream by still returning a
    (possibly empty) list from a *successfully parsed* payload.

    html.unescape() is required, not optional: confirmed live (2026-08-18) that this
    particular script tag's actual page source contains literal HTML-entity sequences
    (&quot; instead of ") inside its JSON — and <script> is an HTML "raw text" element, so
    the browser's parser never decodes entities inside it the way it would for a normal text
    node. .text_content() faithfully returns exactly what's in the source, entities and all,
    so without this the JSON is unparseable every single time, not just sometimes."""
    try:
        raw = page.locator(f"script#{CONTEXT_SCRIPT_ID}").text_content(timeout=5000)
    except PlaywrightTimeoutError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(html.unescape(raw))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _rows_from_context(data: dict) -> List[dict]:
    """[{"caption": str, "views": int|None, "likes": int|None}, ...] from the parsed
    __Creator_Center_Context__ payload's firstBatchQueryItems.item_list (desc/play_count/
    like_count) — confirmed field names from a real content-list snapshot on 2026-08-18.
    A malformed individual item is skipped rather than losing the whole batch."""
    items = ((data.get("firstBatchQueryItems") or {}).get("item_list")) or []
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append({
            "caption": str(item.get("desc") or "").strip(),
            "views": _coerce_int(item.get("play_count")),
            "likes": _coerce_int(item.get("like_count")),
        })
    return rows


def _fetch_content_list_via_dom(page) -> List[dict]:
    """Fallback path, only reached if the embedded-JSON extraction above fails (the script
    tag itself went missing — real TikTok markup drift). Best-effort DOM/text-pattern
    scraping — see the module docstring for why this can't reliably recover views/likes even
    when it works (no "views"/"likes" words sit next to the numbers in the real per-row DOM),
    kept only so a caption (and thus a match) can still be recovered without them."""
    try:
        page.wait_for_selector(
            "[data-e2e='content-item'], [class*='ContentItem'], [class*='content-item']",
            timeout=8000,
        )
    except PlaywrightTimeoutError:
        page.wait_for_timeout(4000)

    items = page.locator("[data-e2e='content-item']").all()
    if not items:
        items = page.locator("[class*='ContentItem'], [class*='content-item']").all()

    rows = []
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
    return rows


def fetch_content_list(headless: bool = True) -> List[dict]:
    """[{"caption": str, "views": int | None, "likes": int | None}, ...] for every post in
    the TikTok Studio content list. Returns [] (never raises) on any failure — a broken
    fetch must degrade gracefully, not crash the loop."""
    cookies = tiktok_uploader.load_cookies()
    if not cookies:
        logger.error(
            "No usable cookies found in %s — cannot fetch TikTok metrics.", tiktok_uploader.COOKIES_PATH,
        )
        return []

    rows: List[dict] = []
    try:
        with sync_playwright() as pw:
            browser = None
            context = None
            try:
                # Launching/context-setup used to sit outside this inner try, so a rejected
                # cookie would skip straight to the finally below without it ever running —
                # leaking the browser process instead of closing it (found in review,
                # 2026-08-18; same fix applied to tiktok_uploader.upload_video()).
                browser = pw.chromium.launch(headless=headless)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.set_default_timeout(NAV_TIMEOUT_MS)
                context.add_cookies(cookies)
                page = context.new_page()

                page.goto(CONTENT_URL, wait_until="domcontentloaded")

                context_data = _extract_context_json(page)
                if context_data is not None:
                    rows = _rows_from_context(context_data)
                else:
                    logger.warning(
                        "%s script tag not found or unparseable — falling back to DOM "
                        "scraping (views/likes may come back empty; see module docstring)",
                        CONTEXT_SCRIPT_ID,
                    )
                    rows = _fetch_content_list_via_dom(page)
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
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


def _match_uploaded_to_content_rows(uploaded: List[dict], content_rows: List[dict]) -> Dict[str, dict]:
    """Matches each uploaded clip's sidecar caption to a scraped content row via
    _captions_match(). A brute-force nested scan is O(len(uploaded) x len(content_rows)) —
    fine at today's single-digit clip volume, but won't hold up if upload volume grows the
    way the system is nominally designed to.

    _captions_match() compares a *variable-length* prefix — min(40, len(a), len(b)) — of
    both captions. A LONG sidecar (>= CAPTION_MATCH_PREFIX_LEN) can only ever match a row
    whose own first CAPTION_MATCH_PREFIX_LEN characters are identical, so long sidecars get
    an O(1) bucket lookup (plus the short rows, in case one is shorter than the window). A
    SHORT sidecar's true prefix_len depends on whatever row it's compared against — it can
    match a long row too (if that row happens to start with the short sidecar text) — so
    short sidecars fall back to scanning every row, not just the short ones. This never
    returns a different result than the brute-force version would; it only reaches it
    faster in the common case, which in practice is the long-sidecar one (real captions
    carry 5-7 hashtags and routinely run well past 40 characters)."""
    long_index: Dict[str, List[dict]] = {}
    short_rows: List[dict] = []
    for row in content_rows:
        caption = row.get("caption", "").strip()
        if len(caption) >= CAPTION_MATCH_PREFIX_LEN:
            long_index.setdefault(caption[:CAPTION_MATCH_PREFIX_LEN].lower(), []).append(row)
        else:
            short_rows.append(row)

    matches: Dict[str, dict] = {}
    for entry in uploaded:
        sidecar_caption = entry.get("caption", "").strip()

        if len(sidecar_caption) >= CAPTION_MATCH_PREFIX_LEN:
            candidates = short_rows + long_index.get(sidecar_caption[:CAPTION_MATCH_PREFIX_LEN].lower(), [])
        else:
            candidates = content_rows

        match = next((row for row in candidates if _captions_match(sidecar_caption, row["caption"])), None)
        if match is not None:
            matches[entry["_clip_id"]] = match

    return matches


def load_viral_memory(path: Path = None) -> Dict[str, dict]:
    # path=None, resolved dynamically below rather than bound as a default-argument value —
    # see streamers.py's load_streamers() for why (a bound default silently ignores a later
    # monkeypatch/reassignment of the module-level constant). Same pattern in every
    # path-defaulting function in this file.
    if path is None:
        path = VIRAL_MEMORY_PATH
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s, starting fresh: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def save_viral_memory(memory: Dict[str, dict], path: Path = None) -> None:
    if path is None:
        path = VIRAL_MEMORY_PATH
    atomic_io.atomic_write_json(path, memory)
    logger.info("Saved %d entrie(s) to %s", len(memory), path)


def prune_viral_memory(memory: Dict[str, dict], max_age_days: int = VIRAL_MEMORY_MAX_AGE_DAYS) -> Dict[str, dict]:
    """Drops entries whose checked_at is older than max_age_days. An entry with no
    checked_at (or an unparseable one) is kept rather than guessed at — never prune on
    ambiguity, same principle as auto_pilot.purge_low_scoring_clips's unscored-clip handling."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept = {}
    dropped = 0
    for clip_id, entry in memory.items():
        checked_at = entry.get("checked_at")
        if checked_at:
            try:
                if datetime.fromisoformat(checked_at) < cutoff:
                    dropped += 1
                    continue
            except ValueError:
                pass
        kept[clip_id] = entry
    if dropped:
        logger.info("Pruned %d viral_memory.json entrie(s) older than %d days", dropped, max_age_days)
    return kept


def load_scrape_health(path: Path = None) -> dict:
    if path is None:
        path = SCRAPE_HEALTH_PATH
    if not path.exists():
        return {"consecutive_failures": 0, "last_success": None, "last_failure": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"consecutive_failures": 0, "last_success": None, "last_failure": None}


def _record_scrape_result(success: bool, path: Path = None) -> dict:
    """Persists whether fetch_content_list() actually returned anything this cycle, and
    returns the updated health record. A run of consecutive failures — as opposed to one
    isolated hiccup — is what actually indicates the scraper is broken (selector drift, a
    TikTok layout change, an expired session), not routine flakiness."""
    if path is None:
        path = SCRAPE_HEALTH_PATH
    health = load_scrape_health(path)
    now = datetime.now(timezone.utc).isoformat()
    if success:
        health["consecutive_failures"] = 0
        health["last_success"] = now
    else:
        health["consecutive_failures"] = health.get("consecutive_failures", 0) + 1
        health["last_failure"] = now
    try:
        atomic_io.atomic_write_json(path, health)
    except OSError as e:
        logger.warning("Could not write %s: %s", path, e)
    return health


def _delete_confirmed_upload(clip_id: str) -> None:
    """Permanently deletes the local .mp4 and .json sidecar for a clip that's been matched
    against TikTok's real content list on two SEPARATE poll cycles, not just once — a single
    match could be a transient/flaky read, and a click-based "confirmed" from
    tiktok_uploader.py was already proven unreliable this session (see C-08 in the audit: a
    click can report success while TikTok silently holds the post private, under review).
    Being visible in the account's own real content list on two independent checks, spaced
    by a full poll interval, is a materially stronger, platform-verified signal — this is
    only ever called for a clip_id that was already in viral_memory.json from a previous
    pass (see update_viral_memory()), never on a clip's first sighting. Its view/like history
    already recorded in viral_memory.json is untouched by this — only the local files go."""
    for suffix in (".mp4", ".json"):
        path = UPLOADED_CLIPS_DIR / f"{clip_id}{suffix}"
        try:
            if path.exists():
                path.unlink()
                logger.info("🗑️ Lokal gelöscht (2x auf TikTok bestätigt): %s", path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)


def update_viral_memory(headless: bool = True) -> int:
    """One full pass: match every locally-uploaded clip against the live TikTok content
    list and update its view/like counts in viral_memory.json. Returns how many entries
    were matched and updated this pass.

    A clip matched for the second time across separate passes (i.e. already present in
    viral_memory.json before this pass) also has its local .mp4/.json sidecar permanently
    deleted — see _delete_confirmed_upload() for why two matches, not one."""
    uploaded = load_uploaded_metadata()
    if not uploaded:
        logger.info("No uploaded clips with metadata found in %s", UPLOADED_CLIPS_DIR)
        return 0

    content_rows = fetch_content_list(headless=headless)
    if not content_rows:
        health = _record_scrape_result(success=False)
        streak = health["consecutive_failures"]
        # Distinguishes "nothing to check yet" (handled above, before ever touching the
        # scraper) from an actual scrape failure with real clips waiting to be matched —
        # and escalates to error-level once that failure repeats, so it's loud enough to
        # notice in normal log-based monitoring instead of scrolling past a routine warning.
        if streak >= CONSECUTIVE_FAILURE_ALERT_THRESHOLD:
            logger.error(
                "TikTok content scrape has failed %d cycles in a row with %d uploaded clip(s) "
                "waiting to be matched — the scraper is very likely broken (selector drift or "
                "an expired session), not just having a quiet cycle. Run "
                "verify_tiktok_selectors.py to check.", streak, len(uploaded),
            )
        else:
            logger.warning(
                "Could not fetch any TikTok content rows this cycle (failure #%d) — skipping update", streak,
            )
        return 0
    _record_scrape_result(success=True)

    memory = load_viral_memory()
    previously_confirmed = set(memory.keys())  # captured BEFORE this pass's own updates
    matched = 0
    newly_reconfirmed: List[str] = []

    row_by_clip_id = _match_uploaded_to_content_rows(uploaded, content_rows)
    for entry in uploaded:
        clip_id = entry["_clip_id"]

        match = row_by_clip_id.get(clip_id)
        if match is None:
            continue

        memory[clip_id] = {
            **{k: v for k, v in entry.items() if k != "_clip_id"},
            "views": match["views"],
            "likes": match["likes"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        matched += 1
        if clip_id in previously_confirmed:
            newly_reconfirmed.append(clip_id)

    pruned_memory = prune_viral_memory(memory)
    if matched or len(pruned_memory) != len(memory):
        save_viral_memory(pruned_memory)

    for clip_id in newly_reconfirmed:
        _delete_confirmed_upload(clip_id)

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
