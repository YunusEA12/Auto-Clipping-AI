"""Browser-based TikTok upload bot (Playwright): reuses a persisted login session
(created once via upload_tiktok_browser_login.py) to drive tiktok.com/upload the same way
a human would in a browser.

Why this exists alongside upload_tiktok.py: TikTok's official Content Posting API only
allows an unaudited developer app to send a video to the creator's private inbox as a draft
(see upload_tiktok.py's INIT_UPLOAD_URL docstring) — it cannot publish directly. This script
drives the real tiktok.com upload page instead, so a fully hands-off post is possible.

Note this is NOT TikTok's official, supported integration path — it automates their web UI
rather than calling a documented API, which most platforms' terms of service restrict.
Understand and accept that trade-off for your own account before relying on it.

TikTok's web markup isn't a stable public contract like an API is and changes over time, so
the selectors below are best-effort. Spot-check with --headed after any TikTok UI change.

Usage:
    python upload_tiktok_browser.py clip.mp4 --description "..." --hashtags gaming viral fy
    python upload_tiktok_browser.py clip.mp4 --description "..." --publish   # posts live instead of drafting
"""

import argparse
import logging
from pathlib import Path
from typing import List, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Holds real TikTok session cookies once logged in — never commit this (see .gitignore).
USER_DATA_DIR = Path(".tiktok_browser_profile")

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
NAV_TIMEOUT_MS = 60_000
PROCESSING_TIMEOUT_MS = 120_000
DEFAULT_HASHTAGS = ["#fyp", "#viral", "#shorts", "#gaming"]


def build_caption_text(description: str, hashtags: Optional[List[str]] = None) -> str:
    hashtags = hashtags or []
    hashtags_str = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
    return f"{description.strip()} {hashtags_str}".strip()


def _launch_context(playwright, headless: bool):
    USER_DATA_DIR.mkdir(exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        str(USER_DATA_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 900},
    )
    context.set_default_timeout(NAV_TIMEOUT_MS)
    return context


def upload_video(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
    headless: bool = True,
) -> bool:
    """Upload one clip through the tiktok.com web UI using the persisted login session.

    `publish=False` (the default) clicks "Save as draft" instead of "Post" — a review step
    stays in the loop unless the caller explicitly opts into `--publish`. Never raises;
    returns True on apparent success, False on any failure, so one failed browser upload
    never crashes an unattended pipeline.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        logger.error("Video not found: %s", video_path)
        return False

    if not USER_DATA_DIR.exists():
        logger.error(
            "No saved TikTok browser session found (%s). Run "
            "upload_tiktok_browser_login.py once to log in first.", USER_DATA_DIR,
        )
        return False

    caption = build_caption_text(description, hashtags or DEFAULT_HASHTAGS)

    with sync_playwright() as pw:
        context = _launch_context(pw, headless)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            logger.info("Opening TikTok upload page...")
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")

            if "login" in page.url:
                logger.error(
                    "Not logged in (redirected to %s). Run upload_tiktok_browser_login.py "
                    "again to refresh the session.", page.url,
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
