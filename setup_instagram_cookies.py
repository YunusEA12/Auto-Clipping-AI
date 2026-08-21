"""Extract your already-authenticated Instagram session cookies directly from your host
browser's local cookie storage (Chrome/Edge/Firefox), via browser-cookie3, and save them to
config/instagram_cookies.json in the format upload_instagram_playwright.py's
context.add_cookies() expects.

Why this instead of an interactive Playwright login: a fresh, unauthenticated Playwright
browser hitting Instagram's login form directly is fingerprintable as automation regardless of
who actually types the password, and Meta is known to challenge or lock logins that come
through automation-controlled browsers more aggressively than most platforms (see
upload_instagram_playwright.py's own module docstring). This sidesteps that entirely — no
login form is touched, nothing is automated. It just reads cookies your normal, everyday
browser already has on disk from your own, completely ordinary human login. Mirrors
get_cookies.py's identical reasoning for TikTok.

browser-cookie3 decrypts the browser's cookie store via the OS's own keychain (Windows
DPAPI / macOS Keychain), which only succeeds for the same OS user account that owns the
browser profile — this can only ever extract YOUR OWN local session, never anyone else's.

IMPORTANT — untested end to end: unlike get_cookies.py (its resulting cookies.json confirmed
working against a live TikTok upload), there is no upload_instagram_playwright.py run yet that
has proven these cookies actually authenticate from wherever this script runs. Cookie-based
session hijacking across machines/IPs is exactly the failure mode that killed this project's
earlier YouTube Studio automation attempt (2026-08-21, see README_UPLOAD.md) — Google's
session cookies never authenticated from the VPS at all, most likely because they're IP-bound.
Meta may or may not do the same for Instagram; treat the resulting config/instagram_cookies.json
as a starting point to test with (`upload_instagram_playwright.py <VIDEO> --publish --headed`
against a real, disposable test Reel), not a guaranteed-working credential.

Note: close the target browser (or at least its Instagram tab) before running this —
Chrome/Edge lock their cookie database file while running, which can make extraction fail or
return stale data.

Usage:
    python setup_instagram_cookies.py                # tries chrome, then edge, then firefox
    python setup_instagram_cookies.py --browser edge
"""

import argparse
import logging
from typing import List, Optional

import browser_cookie3

import atomic_io
from upload_instagram_playwright import COOKIES_PATH, REQUIRED_COOKIE_NAME

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

try:
    # See get_cookies.py's identical try/except for why this is optional and Windows-only.
    from shadowcopy.exceptions import RequiresAdminError
except ImportError:
    RequiresAdminError = None

INSTAGRAM_DOMAIN_FRAGMENT = "instagram.com"

BROWSER_LOADERS = [
    ("chrome", browser_cookie3.chrome),
    ("edge", browser_cookie3.edge),
    ("firefox", browser_cookie3.firefox),
]


def _load_instagram_cookiejar(browser: Optional[str] = None):
    """Returns (browser_label, CookieJar) for the first browser that actually has Instagram
    cookies. Raises RuntimeError if none of the tried browsers have any. Mirrors
    get_cookies.py's _load_tiktok_cookiejar() exactly."""
    loaders = BROWSER_LOADERS
    if browser:
        loaders = [pair for pair in BROWSER_LOADERS if pair[0] == browser]
        if not loaders:
            raise ValueError(f"Unknown browser '{browser}', expected one of: {[b for b, _ in BROWSER_LOADERS]}")

    last_error = None
    needed_admin_for: List[str] = []
    for label, loader in loaders:
        try:
            jar = loader(domain_name=INSTAGRAM_DOMAIN_FRAGMENT)
        except Exception as e:
            if RequiresAdminError is not None and isinstance(e, RequiresAdminError):
                logger.warning(
                    "%s: cookie database is locked (browser is open) and reading a Shadow "
                    "Copy of it needs admin rights. Close %s and re-run, or run this "
                    "terminal as Administrator.", label, label,
                )
                needed_admin_for.append(label)
            else:
                logger.warning("Could not read cookies from %s: %s", label, e)
            last_error = e
            continue

        if len(jar) > 0:
            logger.info("Found %d instagram.com cookie(s) in %s", len(jar), label)
            return label, jar
        logger.info("No instagram.com cookies found in %s, trying next browser...", label)

    if needed_admin_for:
        raise RuntimeError(
            f"Could not read cookies from {needed_admin_for} because their browser was open "
            "and reading a locked cookie database needs admin rights. Close the browser "
            "(fully, not just the tab) and re-run, run this terminal as Administrator, or "
            "try a different browser with --browser edge/firefox."
        ) from last_error

    raise RuntimeError(
        f"Could not find any instagram.com cookies in: {[b for b, _ in loaders]}. Make sure "
        "you're logged into instagram.com in one of these browsers, and that the browser is "
        "closed (or at least the Instagram tab) so its cookie database isn't locked."
    ) from last_error


def _cookie_to_dict(cookie) -> dict:
    entry = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": bool(cookie.secure),
        # http.cookiejar doesn't expose the HttpOnly flag — Instagram's auth cookies
        # (sessionid included) are HttpOnly in practice, so mark all extracted cookies as
        # such, same reasoning as get_cookies.py's identical field.
        "httpOnly": True,
    }
    if cookie.expires:
        entry["expires"] = cookie.expires
    return entry


def extract_instagram_cookies(browser: Optional[str] = None) -> List[dict]:
    label, jar = _load_instagram_cookiejar(browser)
    cookies = [_cookie_to_dict(cookie) for cookie in jar]

    names = {c["name"] for c in cookies}
    if REQUIRED_COOKIE_NAME not in names:
        raise RuntimeError(
            f"Found {len(cookies)} instagram.com cookie(s) in {label}, but no "
            f"'{REQUIRED_COOKIE_NAME}' cookie among them — you're probably not logged into "
            "Instagram in that browser. Log into instagram.com in your normal browser first, "
            "then run this again."
        )

    return cookies


def main():
    parser = argparse.ArgumentParser(
        description="Extract Instagram session cookies from your local browser's cookie storage into config/instagram_cookies.json."
    )
    parser.add_argument(
        "--browser", choices=[b for b, _ in BROWSER_LOADERS], default=None,
        help="Which browser to read from (default: try chrome, then edge, then firefox)",
    )
    args = parser.parse_args()

    try:
        cookies = extract_instagram_cookies(args.browser)
    except (RuntimeError, ValueError) as e:
        logger.error("%s", e)
        raise SystemExit(1)

    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_io.atomic_write_json(COOKIES_PATH, cookies)
    atomic_io.secure_file_permissions(COOKIES_PATH)
    logger.info("✅ ERFOLG: %d Cookie(s) gespeichert in %s", len(cookies), COOKIES_PATH)


if __name__ == "__main__":
    main()
