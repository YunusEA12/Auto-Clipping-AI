"""Browser-based TikTok upload bot (Playwright): authenticates by injecting session cookies
from cookies.json into a fresh browser context, then drives the TikTok creator upload page
the same way a human would in a browser — no login step, no captcha, since the session is
already authenticated via the injected cookies.

Why this exists alongside upload_tiktok.py: TikTok's official Content Posting API only
allows an unaudited developer app to send a video to the creator's private inbox as a draft
(see upload_tiktok.py's INIT_UPLOAD_URL docstring) — it cannot publish directly. This script
drives the real TikTok upload page instead, so a fully hands-off post is possible.

See README_UPLOAD.md for exactly how to produce cookies.json. It holds real TikTok session
cookies and must never be committed — see .gitignore. This is NOT TikTok's official,
supported integration path — it automates their web UI rather than calling a documented
API, which most platforms' terms of service restrict. Understand and accept that trade-off
for your own account before relying on it.

TikTok's web markup isn't a stable public contract like an API is and changes over time, so
the selectors below are best-effort. Spot-check with --headed after any TikTok UI change.

Usage:
    python tiktok_uploader.py clip.mp4 --description "..." --hashtags gaming viral fy
    python tiktok_uploader.py clip.mp4 --description "..." --publish   # posts live instead of drafting
"""

import argparse
import json
import logging
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Exported session cookies from an already-logged-in TikTok browser session — never commit
# this (see .gitignore and README_UPLOAD.md).
COOKIES_PATH = Path("cookies.json")

# tiktok.com/creator redirects here in practice; this is the real, current upload page.
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
NAV_TIMEOUT_MS = 60_000
UPLOAD_BAR_TIMEOUT_MS = 180_000
PROCESSING_TIMEOUT_MS = 120_000
UPLOAD_BAR_POLL_MS = 1000
DEFAULT_HASHTAGS = ["#fyp", "#viral", "#shorts", "#gaming"]


def build_caption_text(description: str, hashtags: Optional[List[str]] = None) -> str:
    hashtags = hashtags or []
    hashtags_str = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
    return f"{description.strip()} {hashtags_str}".strip()


def _normalize_cookie(cookie: dict) -> dict:
    """Common cookie-export browser extensions (e.g. Cookie-Editor) use field names that
    differ slightly from what Playwright's context.add_cookies() expects — most notably
    "expirationDate" instead of "expires" — so normalize those instead of failing outright."""
    normalized = dict(cookie)
    if "expirationDate" in normalized and "expires" not in normalized:
        normalized["expires"] = normalized.pop("expirationDate")
    normalized.pop("hostOnly", None)
    normalized.pop("session", None)
    normalized.pop("storeId", None)
    return normalized


def load_cookies() -> Optional[List[dict]]:
    if not COOKIES_PATH.exists():
        return None

    try:
        with open(COOKIES_PATH, "r", encoding="utf-8") as f:
            raw_cookies = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Could not read %s: %s", COOKIES_PATH, e)
        return None

    if not isinstance(raw_cookies, list):
        logger.error("%s must contain a JSON list of cookie objects", COOKIES_PATH)
        return None

    return [_normalize_cookie(c) for c in raw_cookies]


def _wait_for_upload_complete(page) -> None:
    """Wait until the upload progress indicator reaches 100% (or disappears, which TikTok
    also does once processing finishes) before touching the caption editor — filling in the
    caption too early is a common cause of it being silently discarded. Best-effort: if no
    percentage text is ever found (selector drift), falls back to a fixed settle delay
    instead of hard-failing the whole upload."""
    deadline_ms = UPLOAD_BAR_TIMEOUT_MS
    elapsed_ms = 0
    saw_progress_indicator = False

    while elapsed_ms < deadline_ms:
        try:
            progress_text = page.locator("text=/\\d{1,3}\\s*%/").first.text_content(timeout=UPLOAD_BAR_POLL_MS)
        except PlaywrightTimeoutError:
            progress_text = None

        if progress_text:
            saw_progress_indicator = True
            digits = "".join(ch for ch in progress_text if ch.isdigit())
            if digits and int(digits) >= 100:
                logger.info("Upload bar reached 100%%")
                page.wait_for_timeout(1500)
                return
            logger.info("Upload progress: %s", progress_text.strip())
        elif saw_progress_indicator:
            # The percentage element was present and is now gone — TikTok replaces it with
            # the caption editor/preview once processing completes.
            logger.info("Upload progress indicator finished")
            return

        page.wait_for_timeout(UPLOAD_BAR_POLL_MS)
        elapsed_ms += UPLOAD_BAR_POLL_MS

    if not saw_progress_indicator:
        logger.warning(
            "Never found an upload-progress indicator (selector drift?) — falling back to "
            "a fixed settle delay instead of failing the upload."
        )
        page.wait_for_timeout(5000)
    else:
        logger.warning("Upload progress bar did not confirm 100%% within %ds; continuing anyway", deadline_ms // 1000)


def upload_video(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
    headless: bool = True,
) -> bool:
    """Upload one clip through the TikTok creator upload page, authenticating via cookies
    from cookies.json instead of an interactive login.

    `publish=False` (the default) clicks "Save as draft" instead of "Post" — a review step
    stays in the loop unless the caller explicitly opts into `--publish`. Never raises;
    returns True on apparent success, False on any failure, so one failed browser upload
    never crashes an unattended pipeline.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        logger.error("Video not found: %s", video_path)
        return False

    cookies = load_cookies()
    if not cookies:
        logger.error(
            "No usable cookies found in %s. See README_UPLOAD.md for how to export your "
            "TikTok session cookies into that file before uploading.", COOKIES_PATH,
        )
        return False

    caption = build_caption_text(description, hashtags or DEFAULT_HASHTAGS)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(NAV_TIMEOUT_MS)
        context.add_cookies(cookies)
        page = context.new_page()

        try:
            logger.info("Opening TikTok upload page...")
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")

            if "login" in page.url:
                logger.error(
                    "Not logged in (redirected to %s). The cookies in %s are likely "
                    "expired — re-export a fresh set (see README_UPLOAD.md).", page.url, COOKIES_PATH,
                )
                return False

            logger.info("Uploading file: %s", video_path)
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(str(video_path.resolve()))

            logger.info("Waiting for upload to reach 100%%...")
            _wait_for_upload_complete(page)

            logger.info("Filling in caption...")
            caption_box = page.locator("div[contenteditable='true']").first
            caption_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            caption_box.type(caption, delay=15)
            page.wait_for_timeout(2000)

            if publish:
                logger.info("Clicking Post (publishing live)...")
                button = page.get_by_role("button", name="Post", exact=False)
            else:
                logger.info("Clicking Save as draft (safer default)...")
                button = page.get_by_role("button", name="Save as draft", exact=False)

            button.wait_for(state="visible", timeout=PROCESSING_TIMEOUT_MS)
            button.click()
            page.wait_for_timeout(5000)

            logger.info(
                "TikTok upload finished for %s (publish=%s)", video_path.name, publish
            )
            return True

        except PlaywrightTimeoutError as e:
            logger.error("TikTok upload timed out for %s: %s", video_path, e)
            return False
        except Exception as e:
            logger.error("TikTok upload failed for %s: %s", video_path, e)
            return False
        finally:
            context.close()
            browser.close()


def try_upload_clip(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
) -> bool:
    """Non-raising wrapper for automated pipelines (app.py, stream_watcher.py, auto_pilot.py)
    — always headless, catches every failure mode and returns False instead of raising, so
    one failed upload never crashes an unattended loop."""
    return upload_video(video_path, description, hashtags, publish=publish, headless=True)


def main():
    parser = argparse.ArgumentParser(description="Upload a clip to TikTok via browser automation (Playwright).")
    parser.add_argument("video", type=Path, help="Path to the rendered .mp4 clip")
    parser.add_argument("--description", default="", help="Caption/hook text")
    parser.add_argument("--hashtags", nargs="*", default=None, help="Hashtags, with or without leading #")
    parser.add_argument("--publish", action="store_true", help="Post immediately instead of saving as a draft")
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless")
    args = parser.parse_args()

    success = upload_video(
        args.video, args.description, args.hashtags, publish=args.publish, headless=not args.headed
    )
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
