"""Browser-based TikTok upload bot (Playwright): authenticates by injecting session cookies
from cookies.json into a fresh browser context, then drives the TikTok creator upload page
the same way a human would in a browser — no login step, no captcha, since the session is
already authenticated via the injected cookies.

Why this exists alongside upload_tiktok.py: TikTok's official Content Posting API only
allows an unaudited developer app to send a video to the creator's private inbox as a draft
(see upload_tiktok.py's INIT_UPLOAD_URL docstring) — it cannot publish directly. This script
drives the real TikTok upload page instead, so a fully hands-off post is possible.

Run get_cookies.py once to produce cookies.json (a real, visible login you do yourself,
saved straight to Playwright's native cookie format — no browser-extension reformatting).
See README_UPLOAD.md for details. cookies.json holds real TikTok session
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
from typing import List, NamedTuple, Optional, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import logging_setup

logging_setup.configure_logging()
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
CAPTION_FILL_TIMEOUT_MS = 3000
CAPTION_FILL_POLL_MS = 250
POST_CONFIRM_TIMEOUT_MS = 15000
POST_CONFIRM_POLL_MS = 500
DEFAULT_HASHTAGS = ["#fyp", "#viral", "#shorts", "#gaming"]


class UploadOutcome(NamedTuple):
    """success: the click on Post/Save as draft happened without error — the previous,
    only signal this module ever had. confirmed: whether a real post-click signal (a
    redirect away from the upload page, or the upload form disappearing) was actually
    observed, as opposed to just assuming success after a fixed sleep. A click that silently
    failed client-side looks identical to a real success in `success` alone — `confirmed`
    is the added visibility into that distinction (see M-03 in the audit)."""
    success: bool
    confirmed: bool


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


def cookies_status() -> Tuple[bool, str]:
    """Health-check used by app.py's dashboard: (is_ready, detail_message). Reuses
    load_cookies() so there's one source of truth for "is cookies.json usable" instead of
    the dashboard re-parsing the file itself."""
    if not COOKIES_PATH.exists():
        return False, f"{COOKIES_PATH} nicht gefunden"

    cookies = load_cookies()
    if not cookies:
        return False, f"{COOKIES_PATH} ist leer oder ungültig"

    names = {c.get("name") for c in cookies}
    if "sessionid" not in names:
        return False, f"{len(cookies)} Cookie(s) gefunden, aber kein 'sessionid' — Login vermutlich unvollständig"

    return True, f"{len(cookies)} Cookie(s) gefunden, inkl. sessionid"


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


def _wait_for_caption_filled(page, caption_box, expected_text: str) -> None:
    """Polls the caption box's own text content until it actually contains what was typed
    (or a bounded timeout elapses), instead of a flat sleep with no verification that the
    text actually landed — TikTok's caption editor is a rich-text box, not a plain input,
    and occasionally drops fast-typed input entirely. Best-effort: falls back to a short
    settle delay if the text is never confirmed, same pattern as the upload-bar wait above."""
    deadline_ms = CAPTION_FILL_TIMEOUT_MS
    elapsed_ms = 0
    needle = expected_text.strip()[:30].lower()  # a prefix is enough; TikTok may reformat hashtags into pills

    while elapsed_ms < deadline_ms:
        try:
            current_text = (caption_box.text_content(timeout=CAPTION_FILL_POLL_MS) or "").strip().lower()
        except PlaywrightTimeoutError:
            current_text = ""

        if needle and needle in current_text:
            return
        page.wait_for_timeout(CAPTION_FILL_POLL_MS)
        elapsed_ms += CAPTION_FILL_POLL_MS

    logger.warning("Could not confirm caption text landed within %dms; continuing anyway", deadline_ms)


def _wait_for_post_confirmation(page, file_input) -> bool:
    """Polls for a real signal that clicking Post/Save as draft actually did something —
    the page navigating away from the upload URL, or the upload form (the file input) no
    longer being present, either of which TikTok does once a post/draft is actually
    accepted. Returns whether such a signal was observed within POST_CONFIRM_TIMEOUT_MS.
    Best-effort, same as every other wait in this file: TikTok's real post-success UI has
    never been verified against the live site (see C-01 in the audit / verify_tiktok_selectors.py),
    so this can only report what it can actually detect, not guarantee a real success."""
    deadline_ms = POST_CONFIRM_TIMEOUT_MS
    elapsed_ms = 0

    while elapsed_ms < deadline_ms:
        if UPLOAD_URL not in page.url:
            logger.info("Post-click confirmed: page navigated away from the upload URL")
            return True
        try:
            if file_input.count() == 0:
                logger.info("Post-click confirmed: upload form is no longer present")
                return True
        except Exception:
            pass  # a stale/detached locator during navigation isn't an error here

        page.wait_for_timeout(POST_CONFIRM_POLL_MS)
        elapsed_ms += POST_CONFIRM_POLL_MS

    logger.warning(
        "Could not confirm the post/draft actually succeeded within %ds (no redirect or form "
        "teardown observed) — the click happened, but treat this upload as unconfirmed.",
        deadline_ms // 1000,
    )
    return False


def upload_video(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
    headless: bool = True,
) -> UploadOutcome:
    """Upload one clip through the TikTok creator upload page, authenticating via cookies
    from cookies.json instead of an interactive login.

    `publish=False` (the default) clicks "Save as draft" instead of "Post" — a review step
    stays in the loop unless the caller explicitly opts into `--publish`. Never raises;
    returns UploadOutcome(success, confirmed) — `success=False` on any failure, so one failed
    browser upload never crashes an unattended pipeline; `confirmed` distinguishes an actually
    -observed post-click success signal from "we clicked it and hoped" (see M-03 in the audit).
    """
    video_path = Path(video_path)
    if not video_path.exists():
        logger.error("Video not found: %s", video_path)
        return UploadOutcome(success=False, confirmed=False)

    cookies = load_cookies()
    if not cookies:
        logger.error(
            "No usable cookies found in %s. See README_UPLOAD.md for how to export your "
            "TikTok session cookies into that file before uploading.", COOKIES_PATH,
        )
        return UploadOutcome(success=False, confirmed=False)

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
                return UploadOutcome(success=False, confirmed=False)

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
            _wait_for_caption_filled(page, caption_box, caption)

            if publish:
                logger.info("Clicking Post (publishing live)...")
                button = page.get_by_role("button", name="Post", exact=False)
            else:
                logger.info("Clicking Save as draft (safer default)...")
                button = page.get_by_role("button", name="Save as draft", exact=False)

            button.wait_for(state="visible", timeout=PROCESSING_TIMEOUT_MS)
            button.click()
            confirmed = _wait_for_post_confirmation(page, file_input)

            logger.info(
                "TikTok upload finished for %s (publish=%s, confirmed=%s)", video_path.name, publish, confirmed,
            )
            return UploadOutcome(success=True, confirmed=confirmed)

        except PlaywrightTimeoutError as e:
            logger.error("TikTok upload timed out for %s: %s", video_path, e)
            return UploadOutcome(success=False, confirmed=False)
        except Exception as e:
            logger.error("TikTok upload failed for %s: %s", video_path, e)
            return UploadOutcome(success=False, confirmed=False)
        finally:
            context.close()
            browser.close()


def try_upload_clip(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
) -> UploadOutcome:
    """Non-raising wrapper for automated pipelines (app.py, stream_watcher.py, auto_pilot.py)
    — always headless, catches every failure mode and returns UploadOutcome(success=False, ...)
    instead of raising, so one failed upload never crashes an unattended loop."""
    return upload_video(video_path, description, hashtags, publish=publish, headless=True)


def main():
    parser = argparse.ArgumentParser(description="Upload a clip to TikTok via browser automation (Playwright).")
    parser.add_argument("video", type=Path, help="Path to the rendered .mp4 clip")
    parser.add_argument("--description", default="", help="Caption/hook text")
    parser.add_argument("--hashtags", nargs="*", default=None, help="Hashtags, with or without leading #")
    parser.add_argument("--publish", action="store_true", help="Post immediately instead of saving as a draft")
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless")
    args = parser.parse_args()

    outcome = upload_video(
        args.video, args.description, args.hashtags, publish=args.publish, headless=not args.headed
    )
    if outcome.success and not outcome.confirmed:
        print("Upload clicked, but could not be confirmed (no redirect/form-teardown observed) — check manually.")
    raise SystemExit(0 if outcome.success else 1)


if __name__ == "__main__":
    main()
