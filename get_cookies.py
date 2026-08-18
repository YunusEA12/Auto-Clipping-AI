"""Extract your already-authenticated TikTok session cookies directly from your host
browser's local cookie storage (Chrome/Edge/Firefox), via browser-cookie3, and save them to
cookies.json in the format tiktok_uploader.py's context.add_cookies() expects.

Why this instead of an interactive Playwright login: a fresh, unauthenticated Playwright
browser hitting TikTok's login form directly triggers TikTok's bot/rate-limit protections.
This sidesteps that entirely — no login form is touched, nothing is automated. It just reads
cookies your normal, everyday browser already has on disk from your own, completely ordinary
human login.

browser-cookie3 decrypts the browser's cookie store via the OS's own keychain (Windows
DPAPI / macOS Keychain), which only succeeds for the same OS user account that owns the
browser profile — this can only ever extract YOUR OWN local session, never anyone else's.

Note: close the target browser (or at least its TikTok tab) before running this — Chrome/Edge
lock their cookie database file while running, which can make extraction fail or return stale
data.

Usage:
    python get_cookies.py                # tries chrome, then edge, then firefox
    python get_cookies.py --browser edge
"""

import argparse
import json
import logging
from typing import List, Optional

import browser_cookie3

from tiktok_uploader import COOKIES_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TIKTOK_DOMAIN_FRAGMENT = "tiktok.com"
REQUIRED_COOKIE_NAME = "sessionid"

# Tried in this order when --browser isn't given.
BROWSER_LOADERS = [
    ("chrome", browser_cookie3.chrome),
    ("edge", browser_cookie3.edge),
    ("firefox", browser_cookie3.firefox),
]


def _load_tiktok_cookiejar(browser: Optional[str] = None):
    """Returns (browser_label, CookieJar) for the first browser that actually has TikTok
    cookies. Raises RuntimeError if none of the tried browsers have any."""
    loaders = BROWSER_LOADERS
    if browser:
        loaders = [pair for pair in BROWSER_LOADERS if pair[0] == browser]
        if not loaders:
            raise ValueError(f"Unknown browser '{browser}', expected one of: {[b for b, _ in BROWSER_LOADERS]}")

    last_error = None
    for label, loader in loaders:
        try:
            jar = loader(domain_name=TIKTOK_DOMAIN_FRAGMENT)
        except Exception as e:
            logger.warning("Could not read cookies from %s: %s", label, e)
            last_error = e
            continue

        if len(jar) > 0:
            logger.info("Found %d tiktok.com cookie(s) in %s", len(jar), label)
            return label, jar
        logger.info("No tiktok.com cookies found in %s, trying next browser...", label)

    raise RuntimeError(
        f"Could not find any tiktok.com cookies in: {[b for b, _ in loaders]}. Make sure "
        "you're logged into tiktok.com in one of these browsers, and that the browser is "
        "closed (or at least the TikTok tab) so its cookie database isn't locked."
    ) from last_error


def _cookie_to_dict(cookie) -> dict:
    entry = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": bool(cookie.secure),
        # http.cookiejar doesn't expose the HttpOnly flag (it's a browser/JS-only concept,
        # irrelevant to plain HTTP requests) — TikTok's auth cookies (sessionid included)
        # are HttpOnly in practice, so mark all extracted cookies as such. This doesn't
        # affect whether Playwright sends them; it only affects in-page JS access, which
        # tiktok_uploader.py never needs.
        "httpOnly": True,
    }
    if cookie.expires:
        entry["expires"] = cookie.expires
    return entry


def extract_tiktok_cookies(browser: Optional[str] = None) -> List[dict]:
    label, jar = _load_tiktok_cookiejar(browser)
    cookies = [_cookie_to_dict(cookie) for cookie in jar]

    names = {c["name"] for c in cookies}
    if REQUIRED_COOKIE_NAME not in names:
        raise RuntimeError(
            f"Found {len(cookies)} tiktok.com cookie(s) in {label}, but no "
            f"'{REQUIRED_COOKIE_NAME}' cookie among them — you're probably not logged into "
            "TikTok in that browser. Log into tiktok.com in your normal browser first, then "
            "run this again."
        )

    return cookies


def main():
    parser = argparse.ArgumentParser(
        description="Extract TikTok session cookies from your local browser's cookie storage into cookies.json."
    )
    parser.add_argument(
        "--browser", choices=[b for b, _ in BROWSER_LOADERS], default=None,
        help="Which browser to read from (default: try chrome, then edge, then firefox)",
    )
    args = parser.parse_args()

    try:
        cookies = extract_tiktok_cookies(args.browser)
    except (RuntimeError, ValueError) as e:
        logger.error("%s", e)
        raise SystemExit(1)

    COOKIES_PATH.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("✅ ERFOLG: %d Cookie(s) gespeichert in %s", len(cookies), COOKIES_PATH)


if __name__ == "__main__":
    main()
