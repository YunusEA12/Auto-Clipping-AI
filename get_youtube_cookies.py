"""Extract your already-authenticated Google/YouTube Studio session cookies directly from
your host browser's local cookie storage (Chrome/Edge/Firefox), via browser-cookie3, and save
them to youtube_studio_cookies.json.

This is a DIFFERENT credential from upload.py's token.json/client_secret.json — those are
OAuth API credentials for the YouTube Data API (video upload/stats), which has no endpoint
for YouTube Studio's Audio Library. Adding a background track from that library is a
Studio-web-UI-only feature, so it needs an actual logged-in browser session to automate
against, the same way tiktok_uploader.py needs cookies.json rather than a TikTok API token.

Why this instead of an interactive Playwright login: same reasoning as get_cookies.py — a
fresh, unauthenticated Playwright browser hitting Google's login form directly triggers
Google's bot/2FA/suspicious-activity protections aggressively (more so than TikTok's). This
sidesteps that entirely — no login form is touched, nothing is automated. It just reads
cookies your normal, everyday browser already has on disk from your own, completely ordinary
human login.

browser-cookie3 decrypts the browser's cookie store via the OS's own keychain (Windows
DPAPI / macOS Keychain), which only succeeds for the same OS user account that owns the
browser profile — this can only ever extract YOUR OWN local session, never anyone else's.

IMPORTANT — untested: unlike get_cookies.py's TIKTOK_DOMAIN_FRAGMENT/REQUIRED_COOKIE_NAME
(both confirmed against a real live TikTok upload flow), this script's domain/cookie-name
choices are best-effort, based on Google's publicly documented auth cookie families — there is
no youtube_studio_uploader.py yet to actually try these against a live Studio session. Treat
the resulting youtube_studio_cookies.json as a starting point to iterate from, not a
guaranteed-working credential, until that automation exists and is tested against it.

Note: close the target browser (or at least its Google/YouTube tabs) before running this —
Chrome/Edge lock their cookie database file while running, which can make extraction fail or
return stale data.

Usage:
    python get_youtube_cookies.py                # tries chrome, then edge, then firefox
    python get_youtube_cookies.py --browser edge
"""

import argparse
import logging
from pathlib import Path
from typing import List, Optional

import browser_cookie3

import atomic_io

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

try:
    # See get_cookies.py's identical try/except for why this is optional and Windows-only.
    from shadowcopy.exceptions import RequiresAdminError
except ImportError:
    RequiresAdminError = None

YOUTUBE_STUDIO_COOKIES_PATH = Path("youtube_studio_cookies.json")

# Both queried and merged: Google's session cookies are shared across its properties, but
# depending on which site the browser last actively visited, the SID-family cookies needed
# for studio.youtube.com specifically can show up filed under either domain in the browser's
# cookie store.
DOMAIN_FRAGMENTS = ["youtube.com", "google.com"]

# No single cookie plays sessionid's role here the way it does for TikTok — Google's session
# is carried by a family of SID-prefixed cookies, and which exact ones a given browser exposes
# varies. Requiring just one of these present is a reasonable "you're logged in" signal, not a
# guarantee every cookie studio.youtube.com's Audio Library flow will actually need is present.
REQUIRED_COOKIE_NAMES = {"SAPISID", "__Secure-3PSID", "__Secure-1PSID", "SID"}

BROWSER_LOADERS = [
    ("chrome", browser_cookie3.chrome),
    ("edge", browser_cookie3.edge),
    ("firefox", browser_cookie3.firefox),
]


def _load_google_cookiejars(browser: Optional[str] = None):
    """Returns (browser_label, [CookieJar, ...]) for the first browser that has cookies under
    ANY of DOMAIN_FRAGMENTS. Raises RuntimeError if none of the tried browsers have any."""
    loaders = BROWSER_LOADERS
    if browser:
        loaders = [pair for pair in BROWSER_LOADERS if pair[0] == browser]
        if not loaders:
            raise ValueError(f"Unknown browser '{browser}', expected one of: {[b for b, _ in BROWSER_LOADERS]}")

    last_error = None
    needed_admin_for: List[str] = []
    for label, loader in loaders:
        try:
            jars = [loader(domain_name=fragment) for fragment in DOMAIN_FRAGMENTS]
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

        total = sum(len(jar) for jar in jars)
        if total > 0:
            logger.info("Found %d google.com/youtube.com cookie(s) in %s", total, label)
            return label, jars
        logger.info("No google.com/youtube.com cookies found in %s, trying next browser...", label)

    if needed_admin_for:
        raise RuntimeError(
            f"Could not read cookies from {needed_admin_for} because their browser was open "
            "and reading a locked cookie database needs admin rights. Close the browser "
            "(fully, not just the tab) and re-run, run this terminal as Administrator, or "
            "try a different browser with --browser edge/firefox."
        ) from last_error

    raise RuntimeError(
        f"Could not find any google.com/youtube.com cookies in: {[b for b, _ in loaders]}. "
        "Make sure you're logged into a Google account in one of these browsers (visit "
        "studio.youtube.com to confirm), and that the browser is closed (or at least its "
        "Google/YouTube tabs) so its cookie database isn't locked."
    ) from last_error


def _cookie_to_dict(cookie) -> dict:
    entry = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "secure": bool(cookie.secure),
        # See get_cookies.py's identical field for why this is always True.
        "httpOnly": True,
    }
    if cookie.expires:
        entry["expires"] = cookie.expires
    return entry


def extract_youtube_studio_cookies(browser: Optional[str] = None) -> List[dict]:
    label, jars = _load_google_cookiejars(browser)

    # Dedupe by (name, domain): the youtube.com and google.com queries commonly return
    # overlapping cookies (Google's cookies are frequently scoped to .google.com but visible
    # to youtube.com as a related property).
    seen = set()
    cookies: List[dict] = []
    for jar in jars:
        for cookie in jar:
            key = (cookie.name, cookie.domain)
            if key in seen:
                continue
            seen.add(key)
            cookies.append(_cookie_to_dict(cookie))

    names = {c["name"] for c in cookies}
    if not (names & REQUIRED_COOKIE_NAMES):
        raise RuntimeError(
            f"Found {len(cookies)} google.com/youtube.com cookie(s) in {label}, but none of "
            f"{sorted(REQUIRED_COOKIE_NAMES)} among them — you're probably not logged into a "
            "Google account in that browser. Log into studio.youtube.com in your normal "
            "browser first, then run this again."
        )

    return cookies


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract Google/YouTube Studio session cookies from your local browser's cookie "
            "storage into youtube_studio_cookies.json."
        )
    )
    parser.add_argument(
        "--browser", choices=[b for b, _ in BROWSER_LOADERS], default=None,
        help="Which browser to read from (default: try chrome, then edge, then firefox)",
    )
    args = parser.parse_args()

    try:
        cookies = extract_youtube_studio_cookies(args.browser)
    except (RuntimeError, ValueError) as e:
        logger.error("%s", e)
        raise SystemExit(1)

    atomic_io.atomic_write_json(YOUTUBE_STUDIO_COOKIES_PATH, cookies)
    atomic_io.secure_file_permissions(YOUTUBE_STUDIO_COOKIES_PATH)
    logger.info("✅ ERFOLG: %d Cookie(s) gespeichert in %s", len(cookies), YOUTUBE_STUDIO_COOKIES_PATH)


if __name__ == "__main__":
    main()
