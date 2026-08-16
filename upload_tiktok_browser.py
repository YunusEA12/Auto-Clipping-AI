"""Browser-based TikTok upload bot (Playwright): authenticates by injecting session cookies
from tiktok_cookies.json into a fresh browser context, then drives tiktok.com/upload the
same way a human would in a browser.

Why this exists alongside upload_tiktok.py: TikTok's official Content Posting API only
allows an unaudited developer app to send a video to the creator's private inbox as a draft
(see upload_tiktok.py's INIT_UPLOAD_URL docstring) — it cannot publish directly. This script
drives the real tiktok.com upload page instead, so a fully hands-off post is possible.

tiktok_cookies.json holds real TikTok session cookies (export them from an already-logged-in
browser session using a cookie-export extension) and must never be committed — see
.gitignore. This is NOT TikTok's official, supported integration path — it automates their
web UI rather than calling a documented API, which most platforms' terms of service
restrict. Understand and accept that trade-off for your own account before relying on it.

TikTok's web markup isn't a stable public contract like an API is and changes over time, so
the selectors below are best-effort. Spot-check with --headed after any TikTok UI change.

Usage:
    python upload_tiktok_browser.py clip.mp4 --description "..." --hashtags gaming viral fy
    python upload_tiktok_browser.py clip.mp4 --description "..." --publish   # posts live instead of drafting
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
# this (see .gitignore).
COOKIES_PATH = Path("tiktok_cookies.json")

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
NAV_TIMEOUT_MS = 60_000
PROCESSING_TIMEOUT_MS = 120_000
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


def upload_video(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
    headless: bool = True,
) -> bool:
    """Upload one clip through the tiktok.com web UI, authenticating via cookies from
    tiktok_cookies.json instead of an interactive login.

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
            "No usable cookies found in %s. Export your TikTok session cookies (while "
            "logged in via a normal browser) to that file before uploading.", COOKIES_PATH,
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
                    "expired — re-export a fresh set.", page.url, COOKIES_PATH,
                )
                return False

            logger.info("Uploading file: %s", video_path)
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(str(video_path.resolve()))

            # TikTok transcodes/previews the video client-side before the caption editor and
            # post button become interactive — give that a moment to settle.
            page.wait_for_timeout(5000)

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
                "TikTok browser upload finished for %s (publish=%s)", video_path.name, publish
            )
            return True

        except PlaywrightTimeoutError as e:
            logger.error("TikTok browser upload timed out for %s: %s", video_path, e)
            return False
        except Exception as e:
            logger.error("TikTok browser upload failed for %s: %s", video_path, e)
            return False
        finally:
            context.close()
            browser.close()


def try_upload_to_tiktok_browser(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
) -> bool:
    """Non-raising wrapper for automated pipelines (app.py, stream_watcher.py) — always
    headless, matching the naming/contract of upload_tiktok.try_upload_to_tiktok()."""
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
