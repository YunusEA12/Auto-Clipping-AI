"""One-time interactive TikTok login for upload_tiktok_browser.py.

Opens a real (headed) Chrome window against the same persistent profile directory the
upload bot uses, so you can log in manually once — including any 2FA/captcha — and have
the session cookies persist for future unattended uploads. Run this again any time the
saved session expires or gets logged out.

Usage:
    python upload_tiktok_browser_login.py
"""

import logging

from playwright.sync_api import sync_playwright

from upload_tiktok_browser import USER_DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.tiktok.com/login"


def main():
    USER_DATA_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(USER_DATA_DIR), headless=False, viewport={"width": 1280, "height": 900}
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        logger.info(
            "Log in to TikTok in the opened browser window (including any 2FA). Once "
            "you're logged in and can see your feed/profile, come back here and press "
            "Enter to save the session."
        )
        input("Press Enter after logging in...")

        context.close()
        logger.info("Session saved to %s. You can now run upload_tiktok_browser.py.", USER_DATA_DIR)


if __name__ == "__main__":
    main()
