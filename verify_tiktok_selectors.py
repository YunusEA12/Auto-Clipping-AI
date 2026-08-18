"""C-01 diagnostic: checks every CSS/data-e2e selector tiktok_uploader.py and
metrics_tracker.py actually rely on against the REAL, LIVE TikTok Studio UI.

Why this exists: every selector in those two files (file-input locator, caption box,
Post/Save-as-draft buttons, content-list rows, view/like text patterns) was written from
plausible guesswork about TikTok's DOM, never actually verified against the live site. This
script is that verification pass — it navigates to both pages using an authenticated session
and reports, per selector, whether it currently matches anything at all. It does NOT click
Post, does NOT publish, does NOT modify your account in any way — it only reads what's on
the page.

Two ways to get that authenticated session:

  --manual-login   Opens a real, visible browser and waits for YOU to log in by hand —
                    typing your own credentials, solving any captcha, however TikTok wants
                    it. This script never sees or touches your password; it only reads the
                    page after you're done. Use this if get_cookies.py can't extract your
                    session (e.g. the Windows admin-rights/Volume-Shadow-Copy issue — see
                    M-09 in the audit).

  (default)        Uses cookies.json, produced by get_cookies.py.

This deliberately does NOT automate typing a username/password into TikTok's login form —
that's a materially different, and materially riskier, kind of automation than reading an
already-authenticated session's cookies (which is all get_cookies.py or --manual-login's
post-login cookie capture ever does): it means scripting credential entry directly against a
platform's anti-bot protections, which is the same category of thing this project has
already deliberately avoided (see: no anti-detection/stealth measures in tiktok_uploader.py).
A human typing their own password into a browser they can see is not that.

Usage:
    python verify_tiktok_selectors.py                    # uses cookies.json
    python verify_tiktok_selectors.py --manual-login      # log in by hand instead
    python verify_tiktok_selectors.py --headless          # no visible browser (cookies.json only)
    python verify_tiktok_selectors.py --skip-upload-page      # only check the content list
    python verify_tiktok_selectors.py --skip-content-page     # only check the upload page
"""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import atomic_io
import tiktok_uploader
import metrics_tracker

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

AUDIT_DIR = Path("selector_audit")
LOGIN_URL = "https://www.tiktok.com/login"

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


def _wait_for_manual_login(page) -> bool:
    """Opens the login page and blocks on terminal input until the human confirms they've
    logged in by hand — nothing here ever reads, stores, or types a credential. Returns
    whether the page actually looks logged in afterward (best-effort: just checks we're not
    still sitting on a /login URL)."""
    print(f"\nOpening {LOGIN_URL} in the browser window...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    print(
        "\nLog into TikTok in the opened browser window yourself — enter your own "
        "credentials there, solve any captcha TikTok shows you, and wait until you land on "
        "a normal TikTok page (not the login screen)."
    )
    input("Press Enter here once you're logged in and see a normal TikTok page... ")

    if "login" in page.url:
        print(
            "Still looks like a login page — if you weren't finished, log in fully and "
            "press Enter again."
        )
        input("Press Enter once you're actually logged in... ")

    return "login" not in page.url


def _offer_to_save_session_cookies(context) -> None:
    answer = input(
        f"\nSave this logged-in session's cookies to {tiktok_uploader.COOKIES_PATH} so "
        "tiktok_uploader.py/auto_pilot.py can use it too? [y/N] "
    ).strip().lower()
    if answer != "y":
        return

    raw_cookies = context.cookies()
    tiktok_cookies = [c for c in raw_cookies if "tiktok.com" in c.get("domain", "")]
    if not any(c.get("name") == "sessionid" for c in tiktok_cookies):
        print("No 'sessionid' cookie found in this session — not saving.")
        return

    atomic_io.atomic_write_json(tiktok_uploader.COOKIES_PATH, tiktok_cookies)
    atomic_io.secure_file_permissions(tiktok_uploader.COOKIES_PATH)
    print(f"Saved {len(tiktok_cookies)} cookie(s) to {tiktok_uploader.COOKIES_PATH}.")


def run_audit(headless: bool, check_upload: bool, check_content: bool, manual_login: bool) -> None:
    cookies = None
    if not manual_login:
        cookies = tiktok_uploader.load_cookies()
        if not cookies:
            print(
                f"No usable cookies found in {tiktok_uploader.COOKIES_PATH}. Either run "
                "get_cookies.py first (see README_UPLOAD.md), or re-run this script with "
                "--manual-login to log in by hand instead."
            )
            return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()

        try:
            if manual_login:
                if not _wait_for_manual_login(page):
                    print("Still on a login page — aborting the selector check.")
                    return
                _offer_to_save_session_cookies(context)

            if check_upload:
                print(f"\n=== Upload page: {tiktok_uploader.UPLOAD_URL} ===")
                page.goto(tiktok_uploader.UPLOAD_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if "login" in page.url:
                    print("  Redirected to login — the session is expired or wasn't accepted.")
                else:
                    _check_selectors(page, UPLOAD_PAGE_SELECTORS)
                    _save_snapshot(page, "upload_page")

            if check_content:
                print(f"\n=== Content list page: {metrics_tracker.CONTENT_URL} ===")
                page.goto(metrics_tracker.CONTENT_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                if "login" in page.url:
                    print("  Redirected to login — the session is expired or wasn't accepted.")
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
    parser.add_argument(
        "--manual-login", action="store_true",
        help="Log in by hand in a visible browser instead of using cookies.json — nothing "
        "here reads or types your credentials, you do the login yourself.",
    )
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser window (not available with --manual-login)")
    parser.add_argument("--skip-upload-page", action="store_true")
    parser.add_argument("--skip-content-page", action="store_true")
    args = parser.parse_args()

    if args.manual_login and args.headless:
        parser.error("--manual-login needs a visible browser window to log in — drop --headless")

    run_audit(
        headless=args.headless,
        check_upload=not args.skip_upload_page,
        check_content=not args.skip_content_page,
        manual_login=args.manual_login,
    )


if __name__ == "__main__":
    main()
