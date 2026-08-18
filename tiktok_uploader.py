"""Browser-based TikTok upload bot (Playwright): authenticates by injecting session cookies
from cookies.json into a fresh browser context, then drives the TikTok creator upload page
the same way a human would in a browser — no login step, no captcha, since the session is
already authenticated via the injected cookies.

Why this exists alongside upload_tiktok.py: TikTok's official Content Posting API only
allows an unaudited developer app to send a video to the creator's private inbox as a draft
(see upload_tiktok.py's INIT_UPLOAD_URL docstring) — it cannot publish directly. This script
drives the real TikTok upload page instead, so a fully hands-off post is possible.

Run get_cookies.py once to produce cookies.json (a real, visible login you do yourself,
saved straight to Playwright's native cookie format — no browser-extension reformatting).
See README_UPLOAD.md for details. cookies.json holds real TikTok session
cookies and must never be committed — see .gitignore. This is NOT TikTok's official,
supported integration path — it automates their web UI rather than calling a documented
API, which most platforms' terms of service restrict. Understand and accept that trade-off
for your own account before relying on it.

TikTok's web markup isn't a stable public contract like an API is and changes over time, so
the selectors below are best-effort — verified once against the live TikTok Studio UI on
2026-08-18 via verify_tiktok_selectors.py (see C-01 in the audit), not guessed. Spot-check
with --headed (or re-run verify_tiktok_selectors.py) after any TikTok UI change.

SAFETY MODEL (corrected 2026-08-18, superseding an earlier, wrong assumption): the current
TikTok Studio upload flow has no distinct "Save as draft" button — only "Veröffentlichen"
(post_video_button, publishes live) and "Verwerfen" (discard_post_button, deletes the upload
entirely). An earlier version of this module assumed that leaving an upload unclicked and
letting the browser session close would preserve it as a private draft. That assumption was
never independently verified and was WRONG — directly disproven by the account owner
checking TikTok Studio and the mobile app after a real test: the abandoned upload was not
there. TikTok discards an unpublished, unclicked upload; it does not save it anywhere.

There is therefore no safe "upload but don't publish" outcome available through this
automation path. publish=False is not a safer mode of uploading — it is a refusal to touch
the browser at all: upload_video()/try_upload_clip() with publish=False never open a page,
never spend an upload attempt, and the clip stays exactly where it is on disk. A clip is
either genuinely, verifiably published live (publish=True), or TikTok is never contacted.
Callers that want a human review step before publishing must keep the rendered file local
and let a person decide — this module no longer offers any in-between state.

Usage:
    python tiktok_uploader.py clip.mp4 --description "..." --publish   # the only way this actually posts anything
    python tiktok_uploader.py clip.mp4 --description "..."             # no-op by design — see SAFETY MODEL above
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

# Exported session cookies from an already-logged-in TikTok browser session — never commit
# this (see .gitignore and README_UPLOAD.md).
COOKIES_PATH = Path("cookies.json")

# tiktok.com/creator redirects here in practice; this is the real, current upload page.
UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
NAV_TIMEOUT_MS = 60_000
UPLOAD_BAR_TIMEOUT_MS = 180_000
PROCESSING_TIMEOUT_MS = 120_000
UPLOAD_BAR_POLL_MS = 1000
CAPTION_FILL_TIMEOUT_MS = 3000
CAPTION_FILL_POLL_MS = 250
POST_CONFIRM_TIMEOUT_MS = 15000
POST_CONFIRM_POLL_MS = 500
HASHTAG_SUGGESTION_TIMEOUT_MS = 4000
DEFAULT_HASHTAGS = ["#fyp", "#viral", "#shorts", "#gaming"]

# Diagnostic-only, gated on headless=False (see upload_video()): the URL-change/form-gone
# signal _wait_for_post_confirmation() checks for is itself unverified against what TikTok
# actually shows a human on screen right after the click — a toast, a captcha, an error
# dialog, or nothing at all. This pauses long enough after the click for a human watching a
# --headed run to actually see it, before any confirmation heuristic has a chance to move
# past it. Never fires for headless=True — every automated caller (auto_pilot.py, app.py,
# stream_watcher.py) always passes headless=True, so this can't leak into unattended runs.
POST_CLICK_INSPECTION_DELAY_MS = 15000
POST_CLICK_SNAPSHOT_DIR = Path("selector_audit")


class UploadOutcome(NamedTuple):
    """success: the click on Post/Save as draft happened without error — the previous,
    only signal this module ever had. confirmed: whether a real post-click signal (a
    redirect away from the upload page, or the upload form disappearing) was actually
    observed, as opposed to just assuming success after a fixed sleep. A click that silently
    failed client-side looks identical to a real success in `success` alone — `confirmed`
    is the added visibility into that distinction (see M-03 in the audit)."""
    success: bool
    confirmed: bool


def build_caption_text(description: str, hashtags: Optional[List[str]] = None) -> str:
    hashtags = hashtags or []
    hashtags_str = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
    return f"{description.strip()} {hashtags_str}".strip()


def _normalize_cookie(cookie: dict) -> dict:
    """Common cookie-export browser extensions (e.g. Cookie-Editor) use field names that
    differ slightly from what Playwright's context.add_cookies() expects — most notably
    "expirationDate" instead of "expires" — so normalize those instead of failing outright."""
    normalized = dict(cookie)
    if "expirationDate" in normalized and "expires" not in normalized:
        normalized["expires"] = normalized.pop("expirationDate")
    normalized.pop("hostOnly", None)
    normalized.pop("session", None)
    normalized.pop("storeId", None)
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
    """Health-check used by app.py's dashboard: (is_ready, detail_message). Reuses
    load_cookies() so there's one source of truth for "is cookies.json usable" instead of
    the dashboard re-parsing the file itself."""
    if not COOKIES_PATH.exists():
        return False, f"{COOKIES_PATH} nicht gefunden"

    cookies = load_cookies()
    if not cookies:
        return False, f"{COOKIES_PATH} ist leer oder ungültig"

    names = {c.get("name") for c in cookies}
    if "sessionid" not in names:
        return False, f"{len(cookies)} Cookie(s) gefunden, aber kein 'sessionid' — Login vermutlich unvollständig"

    return True, f"{len(cookies)} Cookie(s) gefunden, inkl. sessionid"


def _wait_for_upload_complete(page) -> bool:
    """Wait until the upload finishes before touching the caption editor — filling in the
    caption too early is a common cause of it being silently discarded. Returns whether a
    real completion signal was actually observed (as opposed to falling back to a blind
    settle delay), so the caller can tell a confirmed upload apart from a guess.

    Primary signal (verified against the live UI): [data-e2e='upload_status_container']
    shows a CheckCircleFill icon once processing succeeds. Secondary/legacy signal: a "N%"
    progress text reaching 100 or disappearing — kept as a second check since it costs
    nothing and may still fire in some states the icon doesn't. Falls back to a fixed settle
    delay only if neither is ever observed."""
    deadline_ms = UPLOAD_BAR_TIMEOUT_MS
    elapsed_ms = 0
    saw_progress_indicator = False
    success_icon = page.locator("[data-e2e='upload_status_container'] [data-icon='CheckCircleFill']")

    while elapsed_ms < deadline_ms:
        try:
            if success_icon.count() > 0:
                logger.info("Upload finished (success icon present)")
                page.wait_for_timeout(500)
                return True
        except Exception:
            pass  # a stale locator mid-navigation isn't an error here

        try:
            progress_text = page.locator("text=/\\d{1,3}\\s*%/").first.text_content(timeout=UPLOAD_BAR_POLL_MS)
        except PlaywrightTimeoutError:
            progress_text = None

        if progress_text:
            saw_progress_indicator = True
            digits = "".join(ch for ch in progress_text if ch.isdigit())
            if digits and int(digits) >= 100:
                logger.info("Upload bar reached 100%%")
                page.wait_for_timeout(1500)
                return True
            logger.info("Upload progress: %s", progress_text.strip())
        elif saw_progress_indicator:
            # The percentage element was present and is now gone — TikTok replaces it with
            # the caption editor/preview once processing completes.
            logger.info("Upload progress indicator finished")
            return True

        page.wait_for_timeout(UPLOAD_BAR_POLL_MS)
        elapsed_ms += UPLOAD_BAR_POLL_MS

    if not saw_progress_indicator:
        logger.warning(
            "Never found an upload-progress indicator (selector drift?) — falling back to "
            "a fixed settle delay instead of failing the upload."
        )
        page.wait_for_timeout(5000)
    else:
        logger.warning("Upload progress bar did not confirm 100%% within %ds; continuing anyway", deadline_ms // 1000)
    return False


OVERLAY_DISMISS_TIMEOUT_MS = 3000


def _dismiss_blocking_overlays(page) -> None:
    """Dismisses the two page-covering overlays confirmed to appear on a real upload
    (2026-08-18, caught by an actual end-to-end test run, not the read-only selector check —
    neither ever showed up in a passive .count() scan): TikTok's cookie-consent banner (a
    <tiktok-cookie-banner> web component behind Shadow DOM — Playwright's locators pierce
    that natively, so no special handling needed beyond the button's own text) and a
    react-joyride "New editing features added" onboarding tooltip. Both are one-time/
    contextual and may not appear on every account or every run, so each dismissal is
    best-effort: try briefly, log if not found, never fail the upload over it — but if
    either is left up, every click after it silently fails (an intercepted-pointer-events
    timeout), which is worse than a missed dismissal attempt."""
    try:
        # .first (found in review, 2026-08-18): without it, a rare duplicate/stale element
        # matching the same accessible name raises a strict-mode Error (not a
        # PlaywrightTimeoutError), which was uncaught here and would fail the whole upload.
        decline_button = page.get_by_role("button", name="Decline optional cookies").first
        decline_button.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
        logger.info("Dismissed cookie-consent banner (declined optional cookies)")
    except PlaywrightTimeoutError:
        logger.info("No cookie-consent banner to dismiss")

    try:
        got_it_button = page.get_by_role("button", name="Got it").first
        got_it_button.click(timeout=OVERLAY_DISMISS_TIMEOUT_MS)
        logger.info("Dismissed 'New editing features added' onboarding tooltip")
    except PlaywrightTimeoutError:
        logger.info("No onboarding tooltip to dismiss")


PENDING_REVIEW_CONFIRM_TIMEOUT_MS = 8000


def _confirm_publish_despite_pending_review(page) -> bool:
    """Found live (2026-08-18) via the headed post-click diagnostic pause, from the real
    saved screenshot/HTML: clicking post_video_button doesn't always publish directly — if
    TikTok's own automated content check ("Kurze Inhaltsprüfung") hasn't finished yet at that
    exact moment, a modal appears instead ("Weiter und veröffentlichen? Wir prüfen dein Video
    noch auf mögliche Probleme...") with Abbrechen/"Jetzt veröffentlichen" buttons — the
    actual publish only happens once that second button is clicked. This isn't a distracting
    overlay to dismiss; it's a required step to complete the exact publish action `publish=True`
    already authorized, so clicking "Jetzt veröffentlichen" here is finishing that same
    action, not a new one. Best-effort: not every run hits this (the review can finish before
    the click), so a short timeout with no match is the expected common case, not an error.
    Returns whether the dialog was found and confirmed."""
    try:
        confirm_button = page.get_by_role("button", name="Jetzt veröffentlichen")
        confirm_button.click(timeout=PENDING_REVIEW_CONFIRM_TIMEOUT_MS)
        logger.info(
            "Content review hadn't finished yet — confirmed 'Jetzt veröffentlichen' to "
            "publish anyway (as already authorized by --publish)"
        )
        return True
    except PlaywrightTimeoutError:
        logger.info("No pending-review confirmation dialog appeared — review had already finished")
        return False


def _save_post_click_snapshot(page, label: str) -> None:
    """Best-effort screenshot + HTML dump to selector_audit/ (already gitignored) — a
    durable record of exactly what was on screen at a given moment, independent of whatever
    a human watching a --headed run does or doesn't catch live. Never raises: a failed debug
    snapshot must not take down the upload it's trying to help diagnose."""
    try:
        POST_CLICK_SNAPSHOT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        page.screenshot(path=str(POST_CLICK_SNAPSHOT_DIR / f"{label}_{timestamp}.png"), full_page=True)
        (POST_CLICK_SNAPSHOT_DIR / f"{label}_{timestamp}.html").write_text(page.content(), encoding="utf-8")
        logger.info("Saved post-click snapshot: %s/%s_%s.(png|html)", POST_CLICK_SNAPSHOT_DIR, label, timestamp)
    except Exception as e:
        logger.warning("Could not save post-click snapshot: %s", e)


def _tokenize_hashtag(page, caption_box, tag: str) -> bool:
    """Types one '#tag' and clicks TikTok's own autocomplete suggestion to register it as
    a real hashtag entity, instead of leaving it as plain text. Verified live (2026-08-18)
    that typing hashtags as part of a continuous string — even space-separated — never
    triggers this: a real published test post's own public page data showed
    "textExtra": [] despite the caption visibly containing '#fyp #viral #shorts #gaming'.
    Also verified live that clicking the suggestion converts the just-typed text into the
    entity in place (no duplication) and appends a trailing space, so the next call needs
    no leading space of its own — handled here by checking the box's current text first.
    Best-effort: if no suggestion appears (slow response, or an obscure tag with literally
    no matches), the tag is left as typed plain text rather than blocking the upload.

    Clicks the suggestion marked `.focused` (TikTok's own best-match indicator, confirmed
    live 2026-08-18: for an exact-match tag it carries `aria-selected="true"`) rather than
    just the first item in the list — a plain `.first` would risk clicking a more popular
    but different tag (e.g. "#fypp" instead of "#fyp") if TikTok ever ranks a related
    suggestion above the exact match (found in review, 2026-08-18). Falls back to `.first`
    if nothing is marked focused, since the DOM structure for that case hasn't been
    independently verified."""
    if not tag.startswith("#"):
        tag = f"#{tag}"
    current_text = caption_box.text_content() or ""
    if current_text and not current_text.endswith(" "):
        caption_box.type(" ", delay=15)
    caption_box.type(tag, delay=60)
    try:
        focused = page.locator("[role='option'].hashtag-suggestion-item.focused")
        if focused.count() > 0:
            focused.first.click(timeout=HASHTAG_SUGGESTION_TIMEOUT_MS)
        else:
            page.locator("[role='option'].hashtag-suggestion-item").first.click(
                timeout=HASHTAG_SUGGESTION_TIMEOUT_MS
            )
        return True
    except PlaywrightTimeoutError:
        logger.warning("No hashtag suggestion appeared for '%s' — left as plain text", tag)
        return False


def _wait_for_caption_filled(page, caption_box, expected_text: str) -> None:
    """Polls the caption box's own text content until it actually contains what was typed
    (or a bounded timeout elapses), instead of a flat sleep with no verification that the
    text actually landed — TikTok's caption editor is a rich-text box, not a plain input,
    and occasionally drops fast-typed input entirely. Best-effort: falls back to a short
    settle delay if the text is never confirmed, same pattern as the upload-bar wait above."""
    deadline_ms = CAPTION_FILL_TIMEOUT_MS
    elapsed_ms = 0
    needle = expected_text.strip()[:30].lower()  # a prefix is enough; TikTok may reformat hashtags into pills

    while elapsed_ms < deadline_ms:
        try:
            current_text = (caption_box.text_content(timeout=CAPTION_FILL_POLL_MS) or "").strip().lower()
        except PlaywrightTimeoutError:
            current_text = ""

        if needle and needle in current_text:
            return
        page.wait_for_timeout(CAPTION_FILL_POLL_MS)
        elapsed_ms += CAPTION_FILL_POLL_MS

    logger.warning("Could not confirm caption text landed within %dms; continuing anyway", deadline_ms)


def _wait_for_post_confirmation(page) -> bool:
    """Polls for a real signal that clicking Post actually published the video — the page
    navigating away from the upload URL, or TikTok's own success toast
    (`#TUXToastProvider-topOutlet .TUXTopToast`, confirmed live 2026-08-18 against a genuine
    publish: it read "Veröffentlichtes Video") appearing. Returns whether such a signal was
    observed within POST_CONFIRM_TIMEOUT_MS. Only used for the publish=True path — the
    publish=False path never clicks anything, so there's no click to confirm (see
    upload_video()).

    Previously also checked `file_input.count() == 0` as a second signal — removed
    (2026-08-18) after live evidence showed the `<input type='file'>` element disappears the
    moment a video finishes UPLOADING, well before Post is ever clicked. That made this
    function report `confirmed=True` almost instantly on every single call regardless of
    whether the click did anything at all — a false positive that let every real upload
    failure that day look locally successful (uploaded_clips/ metadata said confirmed=True
    for videos TikTok's own content list never actually received)."""
    deadline_ms = POST_CONFIRM_TIMEOUT_MS
    elapsed_ms = 0

    while elapsed_ms < deadline_ms:
        if UPLOAD_URL not in page.url:
            logger.info("Post-click confirmed: page navigated away from the upload URL")
            return True
        try:
            if page.locator("#TUXToastProvider-topOutlet .TUXTopToast").count() > 0:
                logger.info("Post-click confirmed: success toast observed")
                return True
        except Exception:
            pass  # a stale/detached locator during navigation isn't an error here

        page.wait_for_timeout(POST_CONFIRM_POLL_MS)
        elapsed_ms += POST_CONFIRM_POLL_MS

    # Discovered live 2026-08-18: TikTok's own "Content check lite" feature has a daily quota
    # — once exhausted, a Post click is accepted client-side (no error, no exception) but
    # TikTok never actually publishes, and neither signal above ever fires. Distinguishing
    # this from a generic failure matters: it's not a selector/logic bug to go fix, it's an
    # external rate limit that resets the next day.
    try:
        quota_exhausted = page.get_by_text("check limit for today").count() > 0
    except Exception:
        quota_exhausted = False

    if quota_exhausted:
        logger.warning(
            "Could not confirm the post succeeded — TikTok's own \"Content check lite\" "
            "daily quota is exhausted for this account (visible on the upload page itself: "
            "\"You've reached your check limit for today\"). This is not a bug on our side; "
            "TikTok is silently declining to publish until the quota resets. Uploads will "
            "keep failing this way until then."
        )
    else:
        logger.warning(
            "Could not confirm the post/draft actually succeeded within %ds (no redirect or "
            "success toast observed) — the click happened, but treat this upload as unconfirmed.",
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
    """Upload one clip through the TikTok creator upload page, authenticating via cookies
    from cookies.json instead of an interactive login.

    `publish=False` (the default) is a deliberate no-op: it never opens a browser or touches
    TikTok at all. See the module docstring's SAFETY MODEL section — there is no "upload but
    don't publish" outcome available anymore (TikTok discards an abandoned upload; it does
    not save it as a draft, confirmed directly by the account owner after a real test on
    2026-08-18). Only `publish=True` actually does anything. Never raises; returns
    UploadOutcome(success, confirmed) — `success=False` on any failure (or on the
    publish=False no-op) so one failed browser upload never crashes an unattended pipeline;
    `confirmed` distinguishes an actually-observed success signal from "we hoped" (see M-03
    in the audit).
    """
    video_path = Path(video_path)

    if not publish:
        logger.info(
            "publish=False: not touching the browser or TikTok at all — there is no safe "
            "'upload but don't publish' outcome anymore (see SAFETY MODEL in this module's "
            "docstring). '%s' stays exactly where it is on disk.", video_path,
        )
        return UploadOutcome(success=False, confirmed=False)

    if not video_path.exists():
        logger.error("Video not found: %s", video_path)
        return UploadOutcome(success=False, confirmed=False)

    cookies = load_cookies()
    if not cookies:
        logger.error(
            "No usable cookies found in %s. See README_UPLOAD.md for how to export your "
            "TikTok session cookies into that file before uploading.", COOKIES_PATH,
        )
        return UploadOutcome(success=False, confirmed=False)

    tag_list = list(hashtags or DEFAULT_HASHTAGS)
    caption = build_caption_text(description, tag_list)

    with sync_playwright() as pw:
        browser = None
        context = None
        try:
            # Launching/context-setup used to sit outside this try, so a rejected cookie
            # (e.g. add_cookies() rejecting a sameSite value a browser-extension export used
            # that isn't exactly "Strict"/"Lax"/"None") would raise straight out of
            # upload_video(), breaking its own documented "never raises" contract and
            # leaking the browser process (found in review, 2026-08-18).
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.set_default_timeout(NAV_TIMEOUT_MS)
            context.add_cookies(cookies)
            page = context.new_page()

            logger.info("Opening TikTok upload page...")
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")

            if "login" in page.url:
                logger.error(
                    "Not logged in (redirected to %s). The cookies in %s are likely "
                    "expired — re-export a fresh set (see README_UPLOAD.md).", page.url, COOKIES_PATH,
                )
                return UploadOutcome(success=False, confirmed=False)

            # The cookie-consent banner shows up on page load, before the upload even starts —
            # dismiss it now so it can't be sitting there intercepting clicks later.
            _dismiss_blocking_overlays(page)

            logger.info("Uploading file: %s", video_path)
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(str(video_path.resolve()))

            logger.info("Waiting for upload to reach 100%%...")
            _wait_for_upload_complete(page)

            # The "New editing features added" onboarding tooltip was observed appearing
            # only after the upload finished (2026-08-18) — dismiss again here, right before
            # the caption box needs a real click, not just after page load.
            _dismiss_blocking_overlays(page)

            logger.info("Filling in caption...")
            caption_box = page.locator("[data-e2e='caption_container'] div[contenteditable='true']").first
            caption_box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            caption_box.type(description.strip(), delay=15)

            tokenized = sum(1 for tag in tag_list if _tokenize_hashtag(page, caption_box, tag))
            logger.info("Tokenized %d/%d hashtag(s) as real TikTok tags", tokenized, len(tag_list))

            _wait_for_caption_filled(page, caption_box, caption)

            # publish=False already returned before ever opening the browser (see above) —
            # everything past this point only ever runs with publish=True.
            #
            # Dismiss overlays once more right before this click: caption typing plus one
            # suggestion-click per hashtag takes several seconds, long enough (confirmed
            # live 2026-08-18) for the "New editing features added" onboarding tooltip to
            # appear *after* the two earlier dismissal calls already found nothing — left
            # unhandled, its react-joyride overlay silently intercepts the Post click for
            # the full click timeout.
            _dismiss_blocking_overlays(page)

            logger.info("Clicking Veröffentlichen (Post, publishing live)...")
            button = page.locator("[data-e2e='post_video_button']")
            button.wait_for(state="visible", timeout=PROCESSING_TIMEOUT_MS)
            button.click()
            _confirm_publish_despite_pending_review(page)

            if not headless:
                # Diagnostic only (see POST_CLICK_INSPECTION_DELAY_MS above) — never reached
                # with headless=True, which every automated caller always passes.
                _save_post_click_snapshot(page, "post_click_immediate")
                logger.info(
                    "Headed mode: pausing %ds right after the click, before any confirmation "
                    "check runs, so you can see exactly what TikTok does — a toast, a "
                    "captcha, an error, or nothing at all.", POST_CLICK_INSPECTION_DELAY_MS // 1000,
                )
                page.wait_for_timeout(POST_CLICK_INSPECTION_DELAY_MS)
                _save_post_click_snapshot(page, "post_click_after_wait")

            confirmed = _wait_for_post_confirmation(page)

            logger.info(
                "TikTok upload finished for %s (publish=%s, confirmed=%s)", video_path.name, publish, confirmed,
            )
            return UploadOutcome(success=True, confirmed=confirmed)

        except PlaywrightTimeoutError as e:
            logger.error("TikTok upload timed out for %s: %s", video_path, e)
            return UploadOutcome(success=False, confirmed=False)
        except Exception as e:
            logger.error("TikTok upload failed for %s: %s", video_path, e)
            return UploadOutcome(success=False, confirmed=False)
        finally:
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()


def try_upload_clip(
    video_path: Path,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
) -> UploadOutcome:
    """Non-raising wrapper for automated pipelines (app.py, stream_watcher.py, auto_pilot.py)
    — always headless, catches every failure mode and returns UploadOutcome(success=False, ...)
    instead of raising, so one failed upload never crashes an unattended loop. Like
    upload_video(), publish=False is a no-op that never touches the browser — see SAFETY
    MODEL in this module's docstring. Callers must not treat that False as "it failed";
    check the log for why (either a real failure, or the deliberate no-op)."""
    return upload_video(video_path, description, hashtags, publish=publish, headless=True)


def main():
    parser = argparse.ArgumentParser(description="Upload a clip to TikTok via browser automation (Playwright).")
    parser.add_argument("video", type=Path, help="Path to the rendered .mp4 clip")
    parser.add_argument("--description", default="", help="Caption/hook text")
    parser.add_argument("--hashtags", nargs="*", default=None, help="Hashtags, with or without leading #")
    parser.add_argument(
        "--publish", action="store_true",
        help="Post live. Required — without it this command does nothing at all (no draft "
        "mode exists anymore; see SAFETY MODEL in this module's docstring)",
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless")
    args = parser.parse_args()

    if not args.publish:
        print(
            "Nothing to do without --publish: TikTok has no draft-save action anymore, so "
            f"there is no safe partial upload to perform. {args.video} was not touched. "
            "Re-run with --publish to actually post it live."
        )
        raise SystemExit(0)

    outcome = upload_video(
        args.video, args.description, args.hashtags, publish=args.publish, headless=not args.headed
    )
    if outcome.success and not outcome.confirmed:
        print("Upload clicked, but could not be confirmed (no redirect/form-teardown observed) — check manually.")
    raise SystemExit(0 if outcome.success else 1)


if __name__ == "__main__":
    main()
