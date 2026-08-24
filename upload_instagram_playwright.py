"""Browser-based Instagram Reels upload bot (Playwright): authenticates by injecting session
cookies from config/instagram_cookies.json into a fresh browser context, then drives the real
Instagram web upload flow the same way a human would — no login step, since the session is
already authenticated via the injected cookies. Mirrors tiktok_uploader.py's architecture
deliberately (cookie injection, never-raises UploadOutcome contract, publish-gated safety
model) rather than Meta's Graph API, per the account owner's explicit 2026-08-21 call: the
Graph API's app-review/business-verification setup was too cumbersome for this project's scale.

NOT Instagram's official, supported integration path — it automates their web UI instead of a
documented API, which Meta's Terms of Service generally restrict for automated/non-human
access. Understand and accept that trade-off for your own account before relying on this.
Deliberately no stealth/anti-detection measures here either (same explicit, repeatedly-made
policy as tiktok_uploader.py — see the audit's H-07) — if Instagram's own fingerprinting flags
this as automation, that is accepted, not fought.

UNVERIFIED SELECTORS — read before trusting this unattended: unlike tiktok_uploader.py (every
selector confirmed once against the live TikTok Studio UI via verify_tiktok_selectors.py
before being trusted — see C-01 in the audit), nothing below has been run against a real,
live Instagram session. There was no prior automation to verify these against when this
module was written, only general knowledge of Instagram's web upload flow. Run once with
--headed against a real (private-audience-if-possible) test Reel and watch it work before ever
trusting this in headless/unattended mode — expect to have to adjust selectors below against
whatever Instagram's actual current DOM turns out to be, exactly the same spot-check
discipline tiktok_uploader.py's own docstring asks for after any UI change.

2026-08-21: _select_9_16_aspect_ratio() (crop-tool + ratio selectors; named _select_original_
aspect_ratio() until 2026-08-22) is new and carries the same UNVERIFIED status — added in
response to an account-owner report of Reels arriving cropped top/bottom. 2026-08-22: now run
against 6 real live sessions — the crop-tool click itself works, but the "Original" selection
failed all 6 times (fails open safely). Switched the target from "Original" (ambiguous —
defers to whatever ratio Instagram infers, which has caused BOTH the original over-crop report
AND a later black-bars/letterboxing report on the same already-correct 1080x1920 source) to
the explicit "9:16" option, plus a text-based selector swap (see that function's own updated
docstring for the forensic detail) — itself still unverified. Check the
selector_audit/05b_after_aspect_ratio_selection snapshot after the next real (--headed or
live) run to confirm it actually clicked the right thing before trusting it further.

SAFETY MODEL — same conservative default as tiktok_uploader.py until proven otherwise: this
module does not know whether Instagram's web upload flow has a safe "save as draft" outcome.
Rather than assume one exists (TikTok's own automation once assumed exactly that, wrongly —
see tiktok_uploader.py's own SAFETY MODEL section for the full account of that mistake),
publish=False here is a full no-op: the browser is never opened, Instagram is never touched,
and the clip stays exactly where it is on disk. Only publish=True does anything at all.

Run setup_instagram_cookies.py once to produce config/instagram_cookies.json (reads cookies
directly out of your own already-logged-in desktop browser via browser-cookie3 — no login
form touched, no automation-fingerprinted login attempt). See README_UPLOAD.md.

Usage:
    python upload_instagram_playwright.py clip.mp4 --description "..." --publish   # the only way this posts anything
    python upload_instagram_playwright.py clip.mp4 --description "..." --publish --headed  # watch it happen, for verifying selectors
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import browser_concurrency
import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

# Exported session cookies from an already-logged-in Instagram browser session — never commit
# this (see .gitignore and README_UPLOAD.md). config/ subdirectory per the account owner's own
# request (2026-08-21), distinct from cookies.json (TikTok, repo root) and token.json (YouTube
# OAuth, repo root) — three platforms' credentials are easier to tell apart in three
# differently-named/located files than three files that all look alike at the repo root.
COOKIES_PATH = Path("config/instagram_cookies.json")

HOME_URL = "https://www.instagram.com/"
NAV_TIMEOUT_MS = 60_000
UPLOAD_PROCESSING_TIMEOUT_MS = 180_000
UPLOAD_POLL_MS = 1000
POST_CONFIRM_TIMEOUT_MS = 240000
POST_CONFIRM_POLL_MS = 500
OVERLAY_DISMISS_TIMEOUT_MS = 3000
DEFAULT_HASHTAGS = ["#reels", "#viral", "#fyp", "#gaming"]

# Instagram's real, well-documented primary session-auth cookie (public knowledge, not
# sensitive — same name TikTok happens to use too). Used by cookies_status()/setup script to
# tell "some cookies present" apart from "actually logged in."
REQUIRED_COOKIE_NAME = "sessionid"

# Shared with tiktok_uploader.py's identical convention — same gitignored directory, so a
# first-run selector verification against a headless VPS (no attached display) still leaves a
# durable, inspectable record of what was actually on screen at each step.
SNAPSHOT_DIR = Path("selector_audit")


class UploadOutcome(NamedTuple):
    """Same shape as tiktok_uploader.UploadOutcome, deliberately — upload_manager.py's
    dispatch pattern expects this exact (success, confirmed) contract from every platform.
    success: the publish click happened without an exception. confirmed: a real post-click
    signal (redirect, success toast, or the upload dialog closing) was actually observed, not
    just assumed after a fixed delay — see _wait_for_post_confirmation()'s own docstring for
    why this distinction matters (tiktok_uploader.py's C-08/C-09 history: a click that
    silently failed client-side looked identical to a real success in `success` alone)."""
    success: bool
    confirmed: bool


def build_caption_text(description: str, hashtags: Optional[List[str]] = None) -> str:
    """Unlike tiktok_uploader.py's tokenized hashtag chips (a TikTok-specific UI element),
    Instagram captions are plain text — hashtags are just typed inline as part of the caption
    itself, no special per-tag UI interaction needed. Simpler, and matches Instagram's actual
    known UX rather than inventing a tokenization step with no basis to verify it against."""
    hashtags = hashtags or []
    hashtags_str = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
    return f"{description.strip()} {hashtags_str}".strip()


_SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "Lax",  # matches modern Chrome's actual default treatment of unset SameSite
    "lax": "Lax",
    "strict": "Strict",
}


def _normalize_cookie(cookie: dict) -> dict:
    """Mirrors tiktok_uploader._normalize_cookie's expirationDate handling, plus a fix found
    the hard way on this same project (2026-08-21, youtube_studio_uploader.py, since removed):
    Playwright's add_cookies() requires sameSite to be exactly "Strict"/"Lax"/"None", but
    Chrome-style cookie exports commonly use lowercase/snake_case values
    ("no_restriction", "unspecified") that raise BrowserContext.add_cookies() outright
    otherwise. Applied proactively here from the start rather than waiting to hit the same
    bug again — Instagram cookie exports are exactly as likely to use this export shape as
    the YouTube ones that surfaced it."""
    normalized = dict(cookie)
    if "expirationDate" in normalized and "expires" not in normalized:
        normalized["expires"] = normalized.pop("expirationDate")
    normalized.pop("hostOnly", None)
    normalized.pop("session", None)
    normalized.pop("storeId", None)
    if "sameSite" in normalized:
        normalized["sameSite"] = _SAME_SITE_MAP.get(str(normalized["sameSite"]).lower(), "Lax")
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
    """Health-check mirroring tiktok_uploader.cookies_status() — one source of truth for
    "is config/instagram_cookies.json usable" instead of re-parsing the file elsewhere."""
    if not COOKIES_PATH.exists():
        return False, f"{COOKIES_PATH} nicht gefunden"
    cookies = load_cookies()
    if not cookies:
        return False, f"{COOKIES_PATH} ist leer oder ungültig"
    names = {c.get("name") for c in cookies}
    if REQUIRED_COOKIE_NAME not in names:
        return False, f"{len(cookies)} Cookie(s) gefunden, aber kein '{REQUIRED_COOKIE_NAME}' — Login vermutlich unvollständig"
    return True, f"{len(cookies)} Cookie(s) gefunden, inkl. {REQUIRED_COOKIE_NAME}"


def _save_snapshot(page, label: str) -> None:
    """Best-effort screenshot + HTML dump to selector_audit/ — mirrors
    tiktok_uploader._save_post_click_snapshot() exactly. Never raises: a failed debug snapshot
    must not take down the upload it's trying to help diagnose."""
    try:
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        page.screenshot(path=str(SNAPSHOT_DIR / f"{label}_{timestamp}.png"), full_page=True)
        (SNAPSHOT_DIR / f"{label}_{timestamp}.html").write_text(page.content(), encoding="utf-8")
        logger.info("Saved snapshot: %s/%s_%s.(png|html)", SNAPSHOT_DIR, label, timestamp)
    except Exception as e:
        logger.warning("Could not save snapshot '%s': %s", label, e)


def _is_account_chooser_interstitial(page) -> bool:
    """CONFIRMED live, 2026-08-24 (see the caller's own comment for the incident this closes):
    Instagram's saved-login "choose an account" screen, distinguishable from both the real
    home feed and an actual login-required redirect by these two aria-labels together — found
    in a saved DOM snapshot from a live failing run, not guessed. Checked via .count() (never
    raises) rather than a locator wait, since this only needs to know "is it on the page right
    now", not wait for it to appear."""
    try:
        return (
            page.locator('[aria-label="Use another profile"]').count() > 0
            and page.locator('[aria-label="Create new account"]').count() > 0
        )
    except Exception:
        return False


def _dismiss_blocking_overlays(page) -> None:
    """Best-effort dismissal of Instagram's well-documented post-login interstitials ("Save
    your login info?", "Turn on Notifications", a cookie-consent banner) — which of these (if
    any) actually appear, and their exact button text, is UNVERIFIED against a live session
    (see module docstring). Each attempt is independent and never raises past this function —
    same reasoning as tiktok_uploader._dismiss_blocking_overlays: leaving one up silently
    intercepts every click after it.

    "OK" added 2026-08-21 (CONFIRMED live): selecting a video file triggers a one-time "Video
    posts are now shared as reels" info dialog with a single "OK" button, sitting on top of the
    Crop step and blocking its "Next" control until dismissed."""
    for name in ("OK", "Not Now", "Not now", "Decline optional cookies", "Allow all cookies", "Save Info", "Cancel"):
        try:
            page.get_by_role("button", name=name).first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
            logger.info("Dismissed an overlay button labeled '%s'", name)
        except PlaywrightTimeoutError:
            continue


def _select_9_16_aspect_ratio(page) -> bool:
    """Best-effort: on Instagram's Crop step (see _dismiss_blocking_overlays' own docstring —
    CONFIRMED live to exist, 2026-08-21), click the crop-tool icon and select "9:16" so
    the source video — already correctly rendered at 9:16/1080x1920 edge-to-edge by process.py's
    render pipeline (no black bars ever produced there — see build_filter_complex's cover-fit
    crop for split_screen/full_cam/center_crop) — posts at its real aspect ratio instead of
    whatever this wizard step defaults to. Account-owner report, 2026-08-21: Reels were arriving
    visibly cropped at the top and bottom; account-owner report, 2026-08-22: Reels were instead
    arriving letterboxed with black bars top/bottom, not filling the screen.

    2026-08-22: switched from targeting "Original" to targeting "9:16" explicitly. "Original"
    is ambiguous — it defers to whatever aspect ratio Instagram itself infers for the source
    (from container/rotation metadata, which has already caused both the "too cropped" AND the
    "black bars" symptom above on the SAME already-correctly-rendered 1080x1920 file), whereas
    "9:16" is an explicit, unambiguous target that matches process.py's render output exactly
    regardless of what Instagram infers. Forensic history: svg[aria-label="Select crop"] IS
    confirmed live to open a real "Crop" dialog (role="dialog" aria-label="Crop", with a
    "Back"/"Next" header) — that part works. The old svg[aria-label="Original"]/svg[aria-
    label="9:16"] icon-based guess failed on 6/6 real runs; a saved selector_audit/05b_*
    screenshot showed the ratio picker rendering as plain-text rows ("Original"/"1:1"/"9:16"/
    "16:9"), not svg-icon labels, so the primary strategy here is a text-based locator scoped
    to the Crop dialog, with the old icon-based locator kept as a second fallback in case a
    different account/flow variant still uses it. STILL UNVERIFIED end-to-end for "9:16"
    specifically (no live --headed session available to confirm the fix actually lands the
    click) — check the next real run's selector_audit/05b_* pair before trusting this further,
    same discipline as this module's own docstring already asks for. Never raises and never
    blocks the upload either way: a missed fix here is strictly better than failing the whole
    upload over it."""
    crop_button = 'svg[aria-label="Select crop"]'
    crop_dialog = page.get_by_role("dialog", name="Crop")

    try:
        page.locator(crop_button).first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning(
            "Could not find the crop-tool button (selector drift, or this flow has no crop "
            "step) — skipping explicit '9:16' aspect-ratio selection; Instagram's default "
            "crop may add black bars or clip this clip's top/bottom. Run with --headed to "
            "check the current DOM."
        )
        return False

    # Text-based first (see 2026-08-22 note above — this is what the screenshot evidence
    # actually shows), the old icon-label selector as a fallback for a possible other variant.
    selected = False
    for locator, description in (
        (crop_dialog.get_by_text("9:16", exact=True), "text '9:16' inside the Crop dialog"),
        (page.locator('svg[aria-label="9:16"]'), "svg[aria-label=\"9:16\"]"),
    ):
        try:
            locator.first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
            selected = True
            logger.info("Clicked the aspect-ratio option via %s", description)
            break
        except PlaywrightTimeoutError:
            continue

    if not selected:
        logger.warning(
            "Crop tool opened but no '9:16' option was found (selector drift?) — the "
            "wizard's default aspect ratio will be used instead."
        )

    # Close the crop popover the same way it was opened, regardless of whether "9:16" was
    # found, so it never stays open over the "Next" button on the following step.
    try:
        page.locator(crop_button).first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass

    if selected:
        logger.info("Selected '9:16' aspect ratio to avoid Instagram's default crop/letterboxing")
    return selected


def _open_create_reel_dialog(page) -> bool:
    """Instagram's web upload has no single dedicated "upload page" URL the way TikTok's
    UPLOAD_URL is — it's a modal launched from the home feed's "Create" control (a '+' icon in
    the left nav), which first opens a small submenu ("Post", "Live video", "Ad") before a
    second click on "Post" reveals the actual upload dialog.

    CONFIRMED against a live session (2026-08-21, headless run, see selector_audit/):
    role-based text lookups ("Create"/"New post"/"Post" as link or button names) were flaky in
    two different ways: (1) Instagram's left nav renders icon-only on first paint and only
    grows text labels after some delay/interaction, so a name lookup for the visible text
    "Create" intermittently found zero matches; (2) get_by_role's default substring name
    matching against "Post" also matched unrelated elements elsewhere on the page (e.g. a
    profile's "N posts" stat), and exact=True then found nothing because the real element's
    computed accessible name wasn't the bare string "Post" either (icon aria-label plus a
    nested visible span apparently concatenate into something exact matching never hit).
    Fixed by targeting the icons' `aria-label` attributes directly via CSS
    (svg[aria-label="..."]) instead of role-based name computation — confirmed present and
    unique in the real DOM regardless of the nav's label-visibility state."""
    try:
        page.locator('svg[aria-label="New post"]').first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning("Could not find the 'New post' nav icon to open the create menu")
        return False

    try:
        page.locator('svg[aria-label="Post"]').first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
        return True
    except PlaywrightTimeoutError:
        logger.warning("Create menu opened, but could not find a 'Post' option within it")
        return False


def _wait_for_upload_processing(page) -> bool:
    """Waits for the Crop step's controls to become available after selecting a file — mirrors
    tiktok_uploader._wait_for_upload_complete()'s reasoning (filling the caption too early on
    TikTok was a confirmed cause of it being silently discarded; assumed to be at least as
    risky here until proven otherwise).

    CONFIRMED live (2026-08-21): there is no `role="progressbar"` element at any point in this
    flow — the original implementation polled for one for the entire UPLOAD_PROCESSING_TIMEOUT_MS
    (180s) on every single run, every time, before falling back to its fixed settle delay. The
    real, observed signal is much simpler and much faster in practice (a few seconds for a
    ~35MB clip): the one-time "Video posts are now shared as reels" info dialog (its "OK"
    button) and/or the Crop step's "Next" button appear as soon as the client-side thumbnail
    processing is ready. Poll for either directly instead of wasting 3 minutes waiting on a
    selector that doesn't exist. Still falls back to a fixed settle delay if neither appears in
    time, same fail-safe shape as before."""
    deadline_ms = UPLOAD_PROCESSING_TIMEOUT_MS
    elapsed_ms = 0

    while elapsed_ms < deadline_ms:
        try:
            if page.get_by_role("button", name="OK").count() > 0:
                logger.info("Upload processed: 'OK' info dialog appeared")
                return True
            if page.get_by_role("button", name="Next").count() > 0:
                logger.info("Upload processed: Crop step's 'Next' button appeared")
                return True
        except Exception:
            pass  # a stale locator mid-navigation isn't an error here

        page.wait_for_timeout(UPLOAD_POLL_MS)
        elapsed_ms += UPLOAD_POLL_MS

    logger.warning(
        "Neither the 'OK' info dialog nor the Crop step's 'Next' button appeared within %ds "
        "(selector drift?) — falling back to a fixed settle delay instead of failing the "
        "upload.", deadline_ms // 1000,
    )
    page.wait_for_timeout(8000)
    return False


def _wait_for_post_confirmation(page, start_url: str) -> bool:
    """Polls for a real signal that the Share click actually published the Reel — the page
    navigating away from the URL it was on right before the click, a success toast appearing,
    or Instagram's own "Sharing" progress dialog closing on its own.

    CONFIRMED live (2026-08-21): clicking Share does not publish instantly — Instagram shows
    its own "Sharing" modal (a spinner, title text "Sharing") while it uploads/processes the
    video server-side, exactly the same shape of "real work happens after the click, not
    during it" that this function's docstring already warned about in the abstract. The
    original 20s timeout was nowhere near long enough (this same clip's earlier
    _wait_for_upload_processing step alone took the full 180s before falling back) — closing
    the browser context that early aborted the real in-flight share entirely (verified after
    the fact: the account's profile showed 0 posts, i.e. nothing was actually published).
    Bumped to POST_CONFIRM_TIMEOUT_MS's new value and given an explicit modal-closed signal so
    a normal-length real share doesn't have to fall through to the fixed-delay/unconfirmed
    path just because it took longer than TikTok's equivalent step."""
    deadline_ms = POST_CONFIRM_TIMEOUT_MS
    elapsed_ms = 0
    saw_sharing_dialog = False

    while elapsed_ms < deadline_ms:
        if page.url != start_url:
            logger.info("Post-click confirmed: page navigated away from %s", start_url)
            return True
        try:
            if page.get_by_text("Your reel has been shared").count() > 0:
                logger.info("Post-click confirmed: success message observed")
                return True
        except Exception:
            pass  # a stale/detached locator during navigation isn't an error here

        try:
            sharing_visible = page.get_by_text("Sharing", exact=True).count() > 0
            if sharing_visible:
                saw_sharing_dialog = True
            elif saw_sharing_dialog:
                logger.info("Post-click confirmed: 'Sharing' progress dialog closed on its own")
                return True
        except Exception:
            pass  # same reasoning as above

        page.wait_for_timeout(POST_CONFIRM_POLL_MS)
        elapsed_ms += POST_CONFIRM_POLL_MS

    logger.warning(
        "Could not confirm the Reel actually published within %ds (no redirect, success "
        "message, or 'Sharing' dialog closing observed) — the Share click happened, but treat "
        "this upload as unconfirmed.",
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
    """Upload one clip as an Instagram Reel, authenticating via cookies from
    config/instagram_cookies.json instead of an interactive login.

    publish=False (the default) is a deliberate, total no-op — see SAFETY MODEL in the module
    docstring for why. Only publish=True does anything at all. Never raises; returns
    UploadOutcome(success, confirmed) — success=False on any failure (or the publish=False
    no-op) so one failed browser upload never crashes an unattended pipeline."""
    video_path = Path(video_path)

    if not publish:
        logger.info(
            "publish=False: not touching the browser or Instagram at all — no verified safe "
            "'upload but don't publish' outcome exists for this platform yet (see SAFETY "
            "MODEL in this module's docstring). '%s' stays exactly where it is on disk.",
            video_path,
        )
        return UploadOutcome(success=False, confirmed=False)

    if not video_path.exists():
        logger.error("Video not found: %s", video_path)
        return UploadOutcome(success=False, confirmed=False)

    cookies = load_cookies()
    if not cookies:
        logger.error(
            "No usable cookies found in %s. Run setup_instagram_cookies.py once to export "
            "your Instagram session cookies before uploading.", COOKIES_PATH,
        )
        return UploadOutcome(success=False, confirmed=False)

    caption = build_caption_text(description, list(hashtags or DEFAULT_HASHTAGS))

    try:
        slot = browser_concurrency.browser_slot()
        slot.__enter__()
    except TimeoutError as e:
        # Same reasoning as tiktok_uploader.upload_video()'s identical guard (2026-08-24):
        # browser_slot() now bounds its own wait, and that must not raise straight out of this
        # function — same "never raises" contract the launch/context-setup try below exists for.
        logger.error("Could not get a browser slot for %s: %s", video_path, e)
        return UploadOutcome(success=False, confirmed=False)

    try:
        with sync_playwright() as pw:
            browser = None
            context = None
            try:
                # Launch/context-setup kept inside this try (same fix tiktok_uploader.py needed,
                # 2026-08-18): a rejected cookie must not raise straight out of this function and
                # break its own documented "never raises" contract, or leak the browser process.
                browser = pw.chromium.launch(headless=headless)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.set_default_timeout(NAV_TIMEOUT_MS)
                context.add_cookies(cookies)
                page = context.new_page()

                logger.info("Opening Instagram...")
                page.goto(HOME_URL, wait_until="domcontentloaded")
                _save_snapshot(page, "01_after_goto")

                if "accounts/login" in page.url:
                    logger.error(
                        "Not logged in (redirected to %s). Cookies in %s are likely expired or "
                        "never authenticated from this machine's IP — re-run "
                        "setup_instagram_cookies.py with a fresh export.", page.url, COOKIES_PATH,
                    )
                    _save_snapshot(page, "01b_login_redirect")
                    return UploadOutcome(success=False, confirmed=False)

                # Found live, 2026-08-24: every Instagram upload for 2+ hours failed at
                # _open_create_reel_dialog() below with "Could not find the 'New post' nav
                # icon" — but the saved snapshot showed neither a login redirect nor the real
                # home feed. It was Instagram's saved-login "choose an account"/one-tap chooser
                # interstitial (aria-labels "Use another profile" / "Create new account" —
                # CONFIRMED present in that snapshot's DOM), which a fresh browser context with
                # injected cookies apparently lands on instead of the feed directly. NOT a
                # login/cookie-expiry problem (session cookies were valid; "accounts/login"
                # never matched) and NOT the create-icon selector breaking — a third page state
                # this code had no detection for at all.
                #
                # No safe way to auto-select an account here without a known username to match
                # against (this codebase deliberately keeps zero configured Instagram username —
                # see COOKIES_PATH's own docstring) — guessing which cached account row to click
                # risks posting to the WRONG account, a worse failure than just not posting.
                # One plain reload IS safe to try (never clicks anything) since this exact
                # interstitial is commonly transient on real accounts; if it persists, fail
                # loudly with a distinct, correctly-attributed error instead of the misleading
                # "create icon not found" every prior run logged for what was actually this.
                if _is_account_chooser_interstitial(page):
                    logger.warning(
                        "Landed on Instagram's account-chooser interstitial instead of the "
                        "home feed — retrying once with a plain reload before giving up."
                    )
                    _save_snapshot(page, "01c_account_chooser_interstitial")
                    page.goto(HOME_URL, wait_until="domcontentloaded")
                    _save_snapshot(page, "01d_after_interstitial_reload")
                    if _is_account_chooser_interstitial(page):
                        logger.error(
                            "Still on the account-chooser interstitial after a reload — this "
                            "is not a selector break or an expired session, it's Instagram "
                            "presenting a 'choose an account' screen this automation has no "
                            "safe way to click through blindly (see this function's own "
                            "comment). Needs a one-time --headed run to see what's actually "
                            "happening and confirm the safe way to get past it."
                        )
                        _save_snapshot(page, "01e_account_chooser_persisted")
                        return UploadOutcome(success=False, confirmed=False)
                    logger.info("Reload cleared the interstitial — continuing.")

                _dismiss_blocking_overlays(page)
                _save_snapshot(page, "02_after_dismiss_overlays")

                if not _open_create_reel_dialog(page):
                    _save_snapshot(page, "03b_create_dialog_not_found")
                    return UploadOutcome(success=False, confirmed=False)
                _save_snapshot(page, "03_create_dialog_open")

                logger.info("Uploading file: %s", video_path)
                file_input = page.locator("input[type='file']").first
                file_input.set_input_files(str(video_path.resolve()))
                _save_snapshot(page, "04_file_selected")

                logger.info("Waiting for upload to process...")
                _wait_for_upload_processing(page)
                _dismiss_blocking_overlays(page)
                _save_snapshot(page, "05_after_upload_processing")

                # Fix for clips arriving cropped top/bottom or letterboxed with black bars
                # (account-owner reports, 2026-08-21 and 2026-08-22) — see
                # _select_9_16_aspect_ratio's own docstring. Must run before the wizard "Next" loop
                # below: the Crop step is the first screen after upload processing.
                _select_9_16_aspect_ratio(page)
                _save_snapshot(page, "05b_after_aspect_ratio_selection")

                # Instagram's upload dialog is commonly a multi-step wizard (crop -> filters ->
                # caption); a "Next" control between the file picker and the caption field is
                # expected but UNVERIFIED — best-effort, not required if the flow is actually
                # single-step for Reels specifically.
                for step in range(2):
                    try:
                        page.get_by_role("button", name="Next").first.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
                        logger.info("Clicked 'Next' to advance the upload wizard")
                        page.wait_for_timeout(500)
                        _save_snapshot(page, f"06_wizard_next_{step}")
                    except PlaywrightTimeoutError:
                        break

                logger.info("Filling in caption...")
                caption_box = page.get_by_role("textbox").first
                caption_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                caption_box.type(caption, delay=15)
                _save_snapshot(page, "07_caption_filled")

                start_url = page.url
                logger.info("Clicking Share (publishing live)...")
                # exact=True is load-bearing (CONFIRMED live, 2026-08-21): a non-exact match for
                # "Share" also matches the "Share to" section header on this same screen (Facebook
                # cross-post toggle) — get_by_role's default substring matching would otherwise
                # make this locator resolve to 2 elements and raise a strict-mode violation instead
                # of clicking the actual Share button.
                share_button = page.get_by_role("button", name="Share", exact=True)
                share_button.wait_for(state="visible", timeout=UPLOAD_PROCESSING_TIMEOUT_MS)
                _save_snapshot(page, "08_before_share_click")
                share_button.click()
                _save_snapshot(page, "09_after_share_click")

                confirmed = _wait_for_post_confirmation(page, start_url)
                _save_snapshot(page, "10_after_confirmation_wait")

                logger.info(
                    "Instagram upload finished for %s (publish=%s, confirmed=%s)",
                    video_path.name, publish, confirmed,
                )
                return UploadOutcome(success=True, confirmed=confirmed)

            except PlaywrightTimeoutError as e:
                logger.error("Instagram upload timed out for %s: %s", video_path, e)
                if 'page' in locals():
                    _save_snapshot(page, "99_timeout")
                return UploadOutcome(success=False, confirmed=False)
            except Exception as e:
                logger.error("Instagram upload failed for %s: %s", video_path, e)
                if 'page' in locals():
                    _save_snapshot(page, "99_exception")
                return UploadOutcome(success=False, confirmed=False)
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
    finally:
        slot.__exit__(None, None, None)


def try_upload_clip(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
) -> UploadOutcome:
    """Non-raising wrapper for automated pipelines — always headless, catches every failure
    mode. Mirrors tiktok_uploader.try_upload_clip()'s exact contract: publish=False never
    touches the browser (see SAFETY MODEL); callers must not treat that False as a failure."""
    return upload_video(video_path, description, hashtags, publish=publish, headless=True)


def main():
    parser = argparse.ArgumentParser(description="Upload a clip to Instagram Reels via browser automation (Playwright).")
    parser.add_argument("video", type=Path, help="Path to the rendered .mp4 clip")
    parser.add_argument("--description", default="", help="Caption/hook text")
    parser.add_argument("--hashtags", nargs="*", default=None, help="Hashtags, with or without leading #")
    parser.add_argument(
        "--publish", action="store_true",
        help="Post live. Required — without it this command does nothing at all (see SAFETY "
        "MODEL in this module's docstring)",
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless — use this to verify the selectors above before trusting this unattended")
    args = parser.parse_args()

    if not args.publish:
        print(
            "Nothing to do without --publish: no verified safe partial-upload outcome exists "
            f"for Instagram yet. {args.video} was not touched. Re-run with --publish to "
            "actually post it live."
        )
        raise SystemExit(0)

    outcome = upload_video(args.video, args.description, args.hashtags, publish=args.publish, headless=not args.headed)
    if outcome.success and not outcome.confirmed:
        print("Upload clicked, but could not be confirmed (no redirect/success message observed) — check manually.")
    raise SystemExit(0 if outcome.success else 1)


if __name__ == "__main__":
    main()
