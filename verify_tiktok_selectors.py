"""C-01 diagnostic: checks every CSS/data-e2e selector tiktok_uploader.py and
metrics_tracker.py actually rely on against the REAL, LIVE TikTok Studio UI.

Why this exists: every selector in those two files (file-input locator, caption box,
Post/Save-as-draft buttons, content-list rows, view/like text patterns) was written from
plausible guesswork about TikTok's DOM, never actually verified against the live site. This
script is that verification pass — it navigates to both pages using your real cookies.json
session and reports, per selector, whether it currently matches anything at all. It does
NOT click Post, does NOT publish, does NOT modify your account in any way — it only reads
what's on the page.

This can only be run by a human with a real, logged-in TikTok session (cookies.json) — I
can't run it on your behalf without your account. Run it with --headed so you can watch it
navigate live; it also saves a full-page screenshot and the raw HTML of both pages to
selector_audit/ so you can share that output for a second look without re-running it.

Usage:
    python verify_tiktok_selectors.py                 # headed by default — watch it run
    python verify_tiktok_selectors.py --headless       # no visible browser window
    python verify_tiktok_selectors.py --skip-upload-page     # only check the content list
    python verify_tiktok_selectors.py --skip-content-page    # only check the upload page
"""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import tiktok_uploader
import metrics_tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

AUDIT_DIR = Path("selector_audit")

# (label, selector) pairs actually used in tiktok_uploader.py's upload flow.
UPLOAD_PAGE_SELECTORS = [
    ("file input (video upload)", "input[type='file']"),
    ("caption box", "div[contenteditable='true']"),
    ("Post button", "button:has-text('Post')"),
    ("Save as draft button", "button:has-text('Save as draft')"),
    ("upload progress %% text", "text=/\\d{1,3}\\s*%/"),
]

# (label, selector) pairs actually used in metrics_tracker.py's content-list scrape.
CONTENT_PAGE_SELECTORS = [
    ("content item (primary)", "[data-e2e='content-item']"),
    ("content item (fallback)", "[class*='ContentItem'], [class*='content-item']"),
    ("content item caption", "[data-e2e='content-item-caption'], [class*='caption'], [class*='Caption']"),
]


def _check_selectors(page, selectors) -> None:
    for label, selector in selectors:
        try:
            count = page.locator(selector).count()
        except Exception as e:
            print(f"  [ERROR] {label!r} ({selector!r}): {e}")
            continue
        status = "MATCHES" if count > 0 else "NO MATCH"
        print(f"  [{status:>8}] {label} ({selector!r}) -> {count} element(s)")


def _save_snapshot(page, name: str) -> None:
    AUDIT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    screenshot_path = AUDIT_DIR / f"{name}_{timestamp}.png"
    html_path = AUDIT_DIR / f"{name}_{timestamp}.html"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"  Saved snapshot: {screenshot_path}, {html_path}")
    except Exception as e:
        logger.warning("Could not save snapshot for %s: %s", name, e)


def run_audit(headless: bool, check_upload: bool, check_content: bool) -> None:
    cookies = tiktok_uploader.load_cookies()
    if not cookies:
        print(
            f"No usable cookies found in {tiktok_uploader.COOKIES_PATH} — run get_cookies.py "
            "first (see README_UPLOAD.md)."
        )
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies(cookies)
        page = context.new_page()

        try:
            if check_upload:
                print(f"\n=== Upload page: {tiktok_uploader.UPLOAD_URL} ===")
                page.goto(tiktok_uploader.UPLOAD_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if "login" in page.url:
                    print("  Redirected to login — cookies.json is expired, re-run get_cookies.py.")
                else:
                    _check_selectors(page, UPLOAD_PAGE_SELECTORS)
                    _save_snapshot(page, "upload_page")

            if check_content:
                print(f"\n=== Content list page: {metrics_tracker.CONTENT_URL} ===")
                page.goto(metrics_tracker.CONTENT_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if "login" in page.url:
                    print("  Redirected to login — cookies.json is expired, re-run get_cookies.py.")
                else:
                    _check_selectors(page, CONTENT_PAGE_SELECTORS)
                    _save_snapshot(page, "content_page")

            print(
                "\nDone. This did not click Post, Save as draft, or modify your account in any "
                "way — it only checked which selectors currently match."
            )
            if not headless:
                print("Browser window will stay open for 15s so you can look around...")
                page.wait_for_timeout(15000)
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="Verify tiktok_uploader.py/metrics_tracker.py's selectors against the live TikTok UI.")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser window (default: headed)")
    parser.add_argument("--skip-upload-page", action="store_true")
    parser.add_argument("--skip-content-page", action="store_true")
    args = parser.parse_args()

    run_audit(
        headless=args.headless,
        check_upload=not args.skip_upload_page,
        check_content=not args.skip_content_page,
    )


if __name__ == "__main__":
    main()
