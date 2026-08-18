"""Standalone tool: open a real, visible browser, log into TikTok yourself, then save the
resulting session cookies straight to cookies.json in Playwright's native format.

This exists to replace fiddly third-party cookie-export browser extensions (inconsistent
field names, manual copy-paste, easy to get wrong) with a first-party tool that produces
exactly the shape tiktok_uploader.py's context.add_cookies() expects — no
"expirationDate vs expires" mismatches, nothing to reformat by hand.

Playwright only ever opens the browser window here — YOU perform the actual login
(including any CAPTCHA/2FA) yourself, with your own eyes and mouse. Nothing about the login
itself is automated.

Usage:
    python get_cookies.py
"""

import json
import logging

from playwright.sync_api import sync_playwright

from tiktok_uploader import COOKIES_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.tiktok.com/login"


def grab_cookies() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        logger.info("🌐 Öffne TikTok-Login...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print()
        print("⏳ Bitte logge dich jetzt manuell im geöffneten Browser-Fenster ein")
        print("   (inklusive eventueller Captcha- oder 2FA-Schritte).")
        input("   Drücke danach hier im Terminal Enter, um die Cookies zu speichern...")
        print()

        cookies = context.cookies()
        if not cookies:
            logger.error("Keine Cookies gefunden — wurde der Login im Browser abgeschlossen?")
            browser.close()
            raise SystemExit(1)

        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

        logger.info("✅ ERFOLG: %d Cookie(s) gespeichert in %s", len(cookies), COOKIES_PATH)
        browser.close()


if __name__ == "__main__":
    grab_cookies()
