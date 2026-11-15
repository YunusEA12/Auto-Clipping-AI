"""Open a real browser window, wait for a human to log into TikTok, then save that session
to cookies.json.

Why this exists next to get_cookies.py: Chrome 127+ ties its cookie encryption key to the
Chrome binary (App-Bound Encryption), so get_cookies.py can no longer read an everyday
Chrome login on this machine at all. And why this exists next to
`verify_tiktok_selectors.py --manual-login`: that path blocks on input() in the terminal,
which makes it unusable for anything driving the pipeline non-interactively (an agent, a
scheduled job, a dashboard button). This one polls the browser for the session cookie
instead, so the only thing a person has to do is log in inside the window.

Nothing here types, stores, or reads a credential. The browser is handed to the human, and
this code only watches for the cookie that appears after a successful login.

Usage:
    python scripts/capture_tiktok_session.py
    python scripts/capture_tiktok_session.py --timeout 900 --out cookies.json
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from autoclip.core import atomic_io, logging_setup
from autoclip.publishing import tiktok_uploader

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.tiktok.com/login"
# Landing here after login both proves the session works for the thing we actually care
# about (uploading) and leaves the window on a useful page.
CONFIRM_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
SESSION_COOKIE = "sessionid"
POLL_SECONDS = 2.0


def _tiktok_cookies(context) -> list:
    return [c for c in context.cookies() if "tiktok.com" in c.get("domain", "")]


def _has_session(cookies: list) -> bool:
    return any(c.get("name") == SESSION_COOKIE and c.get("value") for c in cookies)


def capture(out_path: Path, timeout_seconds: float, keep_open: bool) -> int:
    with sync_playwright() as pw:
        # Headed on purpose: a person has to be able to see and use this window.
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
        except Exception as e:
            logger.warning("Could not open %s (%s) — the window is open, navigate there yourself.", LOGIN_URL, e)

        logger.info("")
        logger.info("=" * 68)
        logger.info(" Bitte im geoeffneten Browserfenster bei TikTok anmelden.")
        logger.info(" Mit dem NEUEN Account. Captcha ggf. loesen.")
        logger.info(" Dieses Skript erkennt von selbst, wann du fertig bist —")
        logger.info(" du musst hier nichts eintippen und nichts bestaetigen.")
        logger.info(" Zeitfenster: %d Minuten.", int(timeout_seconds // 60))
        logger.info("=" * 68)
        logger.info("")

        deadline = time.monotonic() + timeout_seconds
        announced = False
        while time.monotonic() < deadline:
            try:
                cookies = _tiktok_cookies(context)
            except Exception:
                # The window can be mid-navigation; just try again on the next tick.
                time.sleep(POLL_SECONDS)
                continue

            if _has_session(cookies):
                if not announced:
                    logger.info("Login erkannt. Pruefe, ob die Session die Upload-Seite oeffnet...")
                    announced = True
                try:
                    page.goto(CONFIRM_URL, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)
                except Exception as e:
                    logger.warning("Konnte die Upload-Seite nicht laden: %s", e)

                if not tiktok_uploader.wait_for_upload_form(page):
                    # A sessionid can exist before the login is fully through (e.g. a
                    # pending verification step). Keep waiting rather than saving a
                    # session that cannot upload.
                    logger.info("Upload-Formular noch nicht erreichbar — warte weiter.")
                    announced = False
                    time.sleep(POLL_SECONDS)
                    continue

                cookies = _tiktok_cookies(context)
                atomic_io.atomic_write_json(out_path, cookies)
                atomic_io.secure_file_permissions(out_path)
                logger.info("")
                logger.info("✅ %d Cookie(s) gespeichert in %s", len(cookies), out_path)
                logger.info("   Upload-Seite erreichbar unter: %s", page.url)
                if keep_open:
                    logger.info("   Fenster bleibt 20s offen.")
                    page.wait_for_timeout(20000)
                return 0

            time.sleep(POLL_SECONDS)

        logger.error(
            "Zeitfenster abgelaufen, ohne dass ein '%s'-Cookie erschienen ist. "
            "Nichts gespeichert — %s bleibt unveraendert.",
            SESSION_COOKIE,
            out_path,
        )
        return 1
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a hand-made TikTok login into cookies.json.")
    parser.add_argument("--out", default=str(tiktok_uploader.COOKIES_PATH), help="Where to write session cookies")
    parser.add_argument("--timeout", type=float, default=900, help="Seconds to wait for the login (default: 900)")
    parser.add_argument("--no-keep-open", action="store_true", help="Close the window immediately after saving")
    args = parser.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    return capture(Path(args.out), args.timeout, keep_open=not args.no_keep_open)


if __name__ == "__main__":
    raise SystemExit(main())
