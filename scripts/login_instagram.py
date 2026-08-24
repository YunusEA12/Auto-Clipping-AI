"""Interactive Instagram re-authentication helper (2026-08-25 incident: the automated upload
flow's one safe reload retry cannot resolve Instagram's own "choose an account" interstitial —
see upload_instagram_playwright._is_account_chooser_interstitial()'s own docstring for the
incident this closes; every Instagram upload has been failing on it since 2026-08-24T01:33 UTC).

Launches a REAL, headed Playwright browser pre-loaded with whatever session cookies already
exist on disk, lets a human operator manually click through the interstitial (or log in from
scratch, if the session has actually expired) in a window they can see, then saves the
resulting authenticated session back to config/instagram_cookies.json — proven-working cookies
straight from the same browser engine the automated pipeline itself uses, not a cross-browser
export. That's the deliberate difference from setup_instagram_cookies.py: this script's cookies
are confirmed to have actually gotten past Instagram's own account-chooser gate in this exact
Playwright/Chromium build before ever being saved; browser-cookie3-exported cookies never get
that same live confirmation (see that script's own "IMPORTANT — untested end to end" docstring
section).

Requires a visible display — headless=False needs somewhere to actually show the window. Run
this over SSH with X11 forwarding (`ssh -X`) or a VNC session to the VPS; if neither is set up,
run it on your own machine instead, with config/instagram_cookies.json copied over first (so
the existing session seeds the browser the same way production does), then copy the refreshed
file back to the VPS afterward.

Usage:
    python scripts/login_instagram.py
    python scripts/login_instagram.py --timeout 300   # seconds to wait for manual resolution
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Repo root on sys.path — every other module here lives at the repo root and imports its
# siblings directly; scripts/ is a new, deliberately separate "ops tools" location
# (2026-08-25) one level down, so it needs this to import upload_instagram_playwright/atomic_io
# the same way when run as `python scripts/login_instagram.py` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

import atomic_io  # noqa: E402
import upload_instagram_playwright as igp  # noqa: E402

import logging_setup  # noqa: E402

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

DEFAULT_WAIT_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive Instagram re-authentication — resolve the account-chooser "
            "interstitial by hand in a visible browser, then save the working session cookies."
        ),
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        help=f"Seconds to wait for the interstitial/login to clear before giving up (default: {DEFAULT_WAIT_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args()

    cookies = igp.load_cookies()
    if cookies:
        logger.info("Seeding the browser with %d existing cookie(s) from %s", len(cookies), igp.COOKIES_PATH)
    else:
        logger.warning(
            "No existing cookies found at %s — starting from a blank session. Log in from "
            "scratch in the browser window that opens.", igp.COOKIES_PATH,
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()

            logger.info("Opening Instagram...")
            page.goto(igp.HOME_URL, wait_until="domcontentloaded")

            if "accounts/login" in page.url:
                logger.info(
                    "Landed on the login page — log in by hand in the browser window "
                    "(username/password, any 2FA prompt, 'Save your login info?', etc.)."
                )
            else:
                igp._dismiss_blocking_overlays(page)

            logger.info(
                "Waiting up to %ds for you to resolve the account-chooser interstitial (or "
                "log in) by hand in the browser window — this script checks automatically, "
                "just interact with the window normally.", args.timeout,
            )

            deadline = time.monotonic() + args.timeout
            resolved = False
            while time.monotonic() < deadline:
                if "accounts/login" not in page.url and not igp._is_account_chooser_interstitial(page):
                    resolved = True
                    break
                time.sleep(POLL_INTERVAL_SECONDS)

            if not resolved:
                logger.error(
                    "Timed out after %ds still on the login page or account-chooser "
                    "interstitial — nothing saved. Re-run with a longer --timeout if you "
                    "need more time.", args.timeout,
                )
                return 1

            logger.info("Resolved — saving the current session's cookies to %s", igp.COOKIES_PATH)
            fresh_cookies = context.cookies()
            igp.COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            atomic_io.atomic_write_json(igp.COOKIES_PATH, fresh_cookies)
            atomic_io.secure_file_permissions(igp.COOKIES_PATH)
            logger.info(
                "Saved %d cookie(s) to %s. The automated pipeline will use these on its next "
                "Instagram upload attempt.", len(fresh_cookies), igp.COOKIES_PATH,
            )
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
