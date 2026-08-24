"""Multi-platform upload dispatcher (2026-08-20): pushes one already-rendered clip to every
configured platform — currently TikTok (tiktok_uploader.py) and YouTube Shorts (upload.py's
upload_clip()) — abstracting per-platform quirks out of auto_pilot.py's/stream_watcher.py's
own upload call sites, which previously called tiktok_uploader.py directly.

Design choices, and why:

- Sequential, not concurrent: uploading to both platforms at once would fire two large
  resumable video uploads over the same connection simultaneously and make either platform's
  own abuse/rate-limit heuristics more likely to flag this as scripted activity than two
  separate, spaced-out uploads. UPLOAD_DELAY_* below is the explicit pacing buffer between
  them (account owner's own request, 2026-08-20).

- The SAME `publish` flag gates both platforms identically. tiktok_uploader.py's own safety
  model (see its module docstring) already treats publish=False as "never touch the browser
  at all" for TikTok, since TikTok's web upload flow has no safe drafts-only outcome. YouTube
  genuinely COULD support a safe private-upload state that publish=False maps to — but doing
  that would silently diverge from what publish=False already means everywhere else in this
  codebase (skip entirely, no API call, nothing new appears in the creator's account), which
  would be a confusing, easy-to-miss inconsistency between platforms. So publish=False skips
  YouTube too, exactly like TikTok — whoever is deciding "should this go live right now" only
  has to reason about one flag, not one meaning per platform.

- A YouTube failure never crashes the calling cycle, and never blocks the TikTok result from
  being reported: TikTok is attempted and resolved FIRST, independently of whatever happens
  to YouTube afterward. Mirrors tiktok_uploader.try_upload_clip()'s own "never raises, always
  returns an outcome" contract, just one level up."""

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional

from filelock import FileLock

from googleapiclient.errors import HttpError

import atomic_io
import tiktok_uploader
import upload as youtube_uploader
import upload_instagram_playwright as instagram_uploader
import upload_ledger

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

# YouTube's channel-level daily upload cap (HttpError 400, reason "uploadLimitExceeded") is a
# completely different failure from the 403 API-quota case just below — it's an account-wide
# ceiling on how many videos the channel may publish per rolling day, not a per-request rate
# limit, and it doesn't clear by trying again a few minutes later. Found live, 2026-08-21:
# with no detection for this specific reason, every upload attempt (first try AND every
# automatic retry_missing_youtube_uploads() cycle, every ~3-7 minutes) kept hammering the API
# and logging an undifferentiated generic error, with no backoff and no indication of when
# it might actually clear.
UPLOAD_LIMIT_BACKOFF_PATH = Path("youtube_upload_backoff.json")
UPLOAD_LIMIT_BACKOFF_MINUTES = 60


def _read_upload_limit_seen_at() -> Optional[datetime]:
    if not UPLOAD_LIMIT_BACKOFF_PATH.exists():
        return None
    try:
        data = json.loads(UPLOAD_LIMIT_BACKOFF_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["seen_at"])
    except (OSError, ValueError, KeyError):
        return None


def _record_upload_limit_hit() -> None:
    atomic_io.atomic_write_json(
        UPLOAD_LIMIT_BACKOFF_PATH, {"seen_at": datetime.now(timezone.utc).isoformat()}
    )


def _upload_limit_backoff_until() -> Optional[datetime]:
    """None if we're clear to try YouTube uploads; otherwise the timestamp backoff clears at.
    Best-effort: a corrupt/unreadable backoff file reads as "not backing off" rather than
    permanently blocking uploads — same fail-open bias as this module's other state reads."""
    seen_at = _read_upload_limit_seen_at()
    if seen_at is None:
        return None
    clears_at = seen_at + timedelta(minutes=UPLOAD_LIMIT_BACKOFF_MINUTES)
    if datetime.now(timezone.utc) >= clears_at:
        return None
    return clears_at

# Random pacing delay between the TikTok and YouTube upload attempts (2026-08-20, explicit
# account-owner request) — staggers the two platforms' API/network traffic instead of firing
# both back-to-back. Only applied when actually publishing (publish=False makes no real calls
# to either platform, so there is nothing to pace out).
UPLOAD_DELAY_MIN_SECONDS = 30
UPLOAD_DELAY_MAX_SECONDS = 60

# 2026-08-24 incident remediation (see the 2026-08-23 forensic audit): on 2026-08-23, TikTok's
# per-account "Content check lite" daily review quota was exhausted by 13:47 and YouTube's
# channel daily upload cap (uploadLimitExceeded, see above) was hit for good by 16:16 — both
# well before the day's streamers had finished producing clips. Nothing previously limited how
# FAST clips could be pushed to either platform, so a burst of simultaneously-live streamers
# front-loaded each platform's entire daily budget into the first half of the day, leaving nine
# more hours of newly-rendered clips with nowhere confirmed to go on those two platforms.
#
# This is a GLOBAL (fleet-wide, cross-process) minimum spacing between successive real upload
# ATTEMPTS on the same platform — not the UPLOAD_DELAY_* stagger above, which only spaces out
# the platforms relative to EACH OTHER within one clip's upload. Enforced in
# _upload_to_tiktok()/_upload_to_youtube() right before the actual network call, i.e. only once
# every other skip condition (already done, duplicate in-flight, YouTube backoff) has been
# ruled out — a call that was going to no-op anyway shouldn't eat into another process's wait.
# Instagram has no known daily cap (see this module's own docstring on why it isn't gated the
# same way as TikTok/YouTube) so it isn't paced here.
#
# Defaults picked to spread roughly the volume seen on 2026-08-23 (151 TikTok, 101 YouTube
# uploads) across a ~16h active-streaming day instead of the first ~6h of it. Tune per platform
# via env var once the real daily quota for each account is better known.
TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS = float(os.environ.get("TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS", "300"))
YOUTUBE_MIN_UPLOAD_INTERVAL_SECONDS = float(os.environ.get("YOUTUBE_MIN_UPLOAD_INTERVAL_SECONDS", "300"))

UPLOAD_PACING_STATE_PATH = Path("upload_pacing_state.json")
UPLOAD_PACING_LOCK_TIMEOUT_SECONDS = 10


def _try_reserve_upload_pacing_slot(platform: str, min_interval_seconds: float) -> bool:
    """2026-08-25 fix: this used to SLEEP out the remaining wait right here — found live the
    next day as a serious regression, not the intended pacing. auto_pilot.py's run_cycle() is
    single-threaded and fully synchronous per streamer (record chunk -> transcribe -> render
    -> critic -> upload -> repeat); a blocking sleep here blocks that ENTIRE loop, not just the
    upload step. With several streamers simultaneously live all sharing this one fleet-wide
    budget, queued reservations stacked into 15-20+ minute sleeps (994s/1191s/836s observed
    live) — during which the affected streamer recorded nothing at all. Silently turned "pace
    the uploads" into "stall the capture pipeline", the same failure mode as the incident this
    was meant to fix, just self-inflicted instead of platform-caused.

    Now a non-blocking check, same idiom as _upload_limit_backoff_until() above: returns True
    and reserves the slot (writes `now` as the new last-attempt) only if the interval has
    genuinely elapsed since the last real attempt; otherwise returns False immediately with no
    side effect at all — no reservation, no wait. The caller skips this cycle's attempt exactly
    like a YouTube-backoff skip (logged, clip stays in output/, picked up by a later cycle),
    letting recording/rendering continue uninterrupted in the meantime."""
    if min_interval_seconds <= 0:
        return True
    with FileLock(str(UPLOAD_PACING_STATE_PATH) + ".lock", timeout=UPLOAD_PACING_LOCK_TIMEOUT_SECONDS):
        try:
            state = json.loads(UPLOAD_PACING_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        now = datetime.now(timezone.utc)
        last_attempt = state.get(platform)
        if last_attempt:
            try:
                elapsed = (now - datetime.fromisoformat(last_attempt)).total_seconds()
            except ValueError:
                elapsed = min_interval_seconds  # unparseable timestamp: fail open, allow it
            if elapsed < min_interval_seconds:
                return False
        state[platform] = now.isoformat()
        atomic_io.atomic_write_json(UPLOAD_PACING_STATE_PATH, state)
        return True

# 2026-08-22 upload-parity audit: in-call retry for a genuinely failed Instagram attempt
# (Playwright crash, navigation timeout, an unhandled dialog) — up to 3 total tries with
# jittered exponential backoff, same shape as llm_utils.call_with_retry's own ladder, just
# tuned for a browser-automation upload (which needs longer gaps than an API call) rather than
# an LLM request. Deliberately does NOT retry a "clicked but unconfirmed" outcome
# (success=True, confirmed=False) — see _upload_to_instagram's own docstring for why retrying
# THAT specific ambiguous state risks a real duplicate Reel post; only a clear success=False
# or a raised exception is retried here.
INSTAGRAM_UPLOAD_MAX_RETRIES = 3
INSTAGRAM_UPLOAD_BASE_DELAY_SECONDS = 30
INSTAGRAM_UPLOAD_MAX_DELAY_SECONDS = 180


class YouTubeOutcome(NamedTuple):
    """Mirrors tiktok_uploader.UploadOutcome's shape (success/detail) rather than reusing it
    directly — YouTube's API gives a real video_id on success, which TikTok's browser-
    automation flow has no equivalent of, and the failure modes are different enough
    (HttpError with a quota code vs. a Playwright timeout) to warrant their own detail
    string instead of overloading TikTok's own outcome type for a different platform."""
    attempted: bool
    success: bool
    video_id: Optional[str] = None
    detail: str = ""

    @property
    def url(self) -> Optional[str]:
        return f"https://youtu.be/{self.video_id}" if self.video_id else None


class InstagramOutcome(NamedTuple):
    """Same shape/reasoning as YouTubeOutcome above — `attempted` distinguishes "genuinely
    not applicable" (publish=False, or this streamer never opted into Instagram) from a real
    failure. Unlike YouTube's Data API, Instagram's Playwright automation has no persistent
    ID to hand back on success, only success/confirmed (see
    upload_instagram_playwright.UploadOutcome's own docstring)."""
    attempted: bool
    success: bool
    confirmed: bool = False
    detail: str = ""


class MultiPlatformOutcome(NamedTuple):
    tiktok: tiktok_uploader.UploadOutcome
    youtube: YouTubeOutcome
    instagram: InstagramOutcome

    @property
    def any_success(self) -> bool:
        tiktok_success = self.tiktok.success and self.tiktok.confirmed
        instagram_success = self.instagram.success and self.instagram.confirmed
        return tiktok_success or self.youtube.success or instagram_success


def _upload_to_youtube(
    video_path: Path, title: str, description: str, tags: Optional[List[str]], publish: bool,
) -> YouTubeOutcome:
    if not publish:
        logger.info("publish=False — skipping YouTube upload for %s (same no-op as TikTok)", video_path.name)
        return YouTubeOutcome(attempted=False, success=False, detail="publish=False, skipped")

    backoff_until = _upload_limit_backoff_until()
    if backoff_until is not None:
        logger.info(
            "Skipping YouTube upload for %s — still backing off after hitting the channel's "
            "daily upload cap (uploadLimitExceeded); next attempt after %s.",
            video_path.name, backoff_until.isoformat(),
        )
        return YouTubeOutcome(attempted=False, success=False, detail="uploadLimitExceeded backoff")

    # Content-hash ledger check (2026-08-21) — see upload_ledger.py's own module docstring for
    # the two real production incidents this closes. Checked BEFORE ever touching the network:
    # a clip already recorded "done" is a pure local no-op, no API call at all.
    content_hash = upload_ledger.compute_content_hash(video_path)
    existing = upload_ledger.get_entry(content_hash, "youtube")
    if existing and existing.get("status") == "done":
        logger.info(
            "Skipping YouTube upload for %s — already uploaded per the ledger (video_id=%s, "
            "hash=%s...); not submitting a duplicate.",
            video_path.name, existing.get("video_id"), content_hash[:12],
        )
        return YouTubeOutcome(attempted=True, success=True, video_id=existing.get("video_id"), detail="already uploaded (ledger)")

    if not _try_reserve_upload_pacing_slot("youtube", YOUTUBE_MIN_UPLOAD_INTERVAL_SECONDS):
        logger.info(
            "Skipping YouTube upload for %s — within the fleet-wide daily-quota pacing "
            "interval (another upload happened too recently); will retry a later cycle.",
            video_path.name,
        )
        return YouTubeOutcome(attempted=False, success=False, detail="daily-quota pacing budget")

    if not upload_ledger.try_mark_pending(content_hash, "youtube", title=title):
        logger.warning(
            "Skipping YouTube upload for %s — another attempt for this exact clip (hash=%s...) "
            "is already in flight or was very recently attempted; not submitting a duplicate.",
            video_path.name, content_hash[:12],
        )
        return YouTubeOutcome(attempted=False, success=False, detail="duplicate upload blocked by ledger (pending)")

    try:
        video_id = youtube_uploader.upload_clip(video_path, title, description, tags)
    except HttpError as e:
        error_text = str(e)
        upload_limit_exceeded = e.resp.status == 400 and "uploadLimitExceeded" in error_text
        # Distinct from uploadLimitExceeded above: this is the *project-level* Data API quota
        # ("Video Uploads" / "Video Uploads per day", reason rateLimitExceeded) rather than the
        # channel's own account-wide cap — a new/unverified OAuth client defaults to a very low
        # daily upload allowance (commonly ~6/day) shared across every streamer this fleet
        # publishes for. Found live, 2026-08-21: with no detection for this specific status/
        # reason pair, it fell into the undifferentiated `else` below — no backoff got recorded,
        # so every streamer's next cycle AND auto_pilot.retry_missing_youtube_uploads() (runs
        # every ~3-7 minutes per streamer) kept resubmitting the exact same upload straight into
        # the same 429, dozens of times a day across the fleet. That's the "flooding" a new
        # channel's abuse heuristics are most likely to key on — fold it into the same backoff
        # as uploadLimitExceeded so a hit here stops the fleet from hammering the endpoint too.
        daily_video_quota_exceeded = (
            e.resp.status == 429
            and "quota exceeded" in error_text.lower()
            and "video uploads" in error_text.lower()
        )
        quota_exceeded = e.resp.status == 403 and "quota" in error_text.lower()
        if upload_limit_exceeded or daily_video_quota_exceeded:
            _record_upload_limit_hit()
            resumes_at = datetime.now(timezone.utc) + timedelta(minutes=UPLOAD_LIMIT_BACKOFF_MINUTES)
            reason = (
                "an account-level limit (uploadLimitExceeded)" if upload_limit_exceeded
                else "the project's daily Data API 'Video Uploads' quota (429 rateLimitExceeded)"
            )
            logger.error(
                "YouTube daily upload ceiling hit for %s — %s, not a transient error worth "
                "retrying immediately. Backing off further YouTube attempts until %s instead of "
                "retrying every cycle (retries against this exact error already observed live, "
                "2026-08-21).",
                video_path.name, reason, resumes_at.isoformat(),
            )
        elif quota_exceeded:
            logger.error("YouTube API quota exceeded — could not upload %s: %s", video_path.name, e)
        else:
            logger.error("YouTube upload failed for %s: %s", video_path.name, e)
        upload_ledger.mark_failed(content_hash, "youtube", detail=error_text)
        return YouTubeOutcome(attempted=True, success=False, detail=error_text)
    except Exception as e:
        # Anything else (OAuth/token problem, network failure, a missing client_secret.json)
        # — never let a YouTube-side problem take down the cycle that's also reporting the
        # TikTok result. upload.upload_clip() itself already self-heals the "raised, but the
        # video actually made it up" case via _find_recent_upload_by_title() before this
        # ever runs — reaching here means it genuinely couldn't verify a success either.
        logger.error("YouTube upload raised unexpectedly for %s: %s", video_path.name, e)
        upload_ledger.mark_failed(content_hash, "youtube", detail=str(e))
        return YouTubeOutcome(attempted=True, success=False, detail=str(e))

    # Background music for YouTube is now mixed directly into the render (process.py's
    # build_audio_filter(), via ffmpeg) before this upload ever runs — dropped the Playwright
    # YouTube Studio automation this step used to call (youtube_studio_uploader.py), which
    # never got past cookie-based login on this VPS (Google's session cookies appear to be
    # IP-bound) and risked the account's OAuth token being flagged for scripted Studio access.
    upload_ledger.mark_done(content_hash, "youtube", video_id=video_id, title=title)
    return YouTubeOutcome(attempted=True, success=True, video_id=video_id)


def _upload_to_instagram(
    video_path: Path, title: str, description: str, tags: Optional[List[str]], publish: bool, instagram_enabled: bool,
) -> InstagramOutcome:
    """instagram_enabled is a SEPARATE gate from publish, not folded into one flag the way
    TikTok/YouTube share `publish` — Instagram's automation is unverified (see
    upload_instagram_playwright.py's own module docstring: no selector has ever been run
    against a live session), so it defaults OFF for every streamer until proven working,
    opted into per-streamer via streamers.json's "instagram" field (see orchestrator.py's
    build_auto_pilot_cmd()) rather than firing automatically the moment this integration
    merges. publish=True alone is NOT sufficient to attempt Instagram — both must be true.

    2026-08-22: retries a genuine failure (an exception, or try_upload_clip() returning
    success=False — a navigation timeout, an unhandled dialog, a crashed browser) up to
    INSTAGRAM_UPLOAD_MAX_RETRIES times with jittered exponential backoff, all within this one
    call, before recording a final "failed" in the ledger — see that constant's own comment.
    Deliberately stops retrying the moment an attempt comes back success=True, even if
    unconfirmed: try_upload_clip() only reaches that state after actually clicking Instagram's
    Share button, so a second attempt risks posting a real duplicate Reel over a first attempt
    that may well have gone through — the same "don't guess on ambiguity" rule
    upload_ledger.mark_unresolved() already applies one layer down for the NEXT cycle's retry."""
    if not publish or not instagram_enabled:
        reason = "publish=False" if not publish else "instagram not enabled for this streamer"
        logger.info("%s — skipping Instagram upload for %s", reason, video_path.name)
        return InstagramOutcome(attempted=False, success=False, detail=reason)

    content_hash = upload_ledger.compute_content_hash(video_path)
    existing = upload_ledger.get_entry(content_hash, "instagram")
    if existing and existing.get("status") == "done":
        logger.info(
            "Skipping Instagram upload for %s — already uploaded per the ledger (hash=%s...); "
            "not submitting a duplicate.", video_path.name, content_hash[:12],
        )
        return InstagramOutcome(attempted=True, success=True, confirmed=True, detail="already uploaded (ledger)")

    if not upload_ledger.try_mark_pending(content_hash, "instagram", title=title):
        logger.warning(
            "Skipping Instagram upload for %s — another attempt for this exact clip "
            "(hash=%s...) is already in flight, was very recently attempted, or its last "
            "attempt's publish status is still unconfirmed and within the cooldown window; "
            "not submitting a duplicate.", video_path.name, content_hash[:12],
        )
        return InstagramOutcome(attempted=False, success=False, detail="duplicate upload blocked by ledger (pending)")

    outcome = None
    last_error = None
    for attempt in range(1, INSTAGRAM_UPLOAD_MAX_RETRIES + 1):
        try:
            outcome = instagram_uploader.try_upload_clip(video_path, description, tags, publish=publish)
            last_error = None
        except Exception as e:
            # Never let an Instagram-side problem take down a cycle that's also reporting
            # TikTok and YouTube results — same reasoning as the YouTube try/except above.
            outcome = None
            last_error = str(e)

        if outcome is not None and outcome.success:
            break  # real success (confirmed or not) -- see docstring for why this stops retrying

        if attempt < INSTAGRAM_UPLOAD_MAX_RETRIES:
            delay = min(
                INSTAGRAM_UPLOAD_MAX_DELAY_SECONDS,
                INSTAGRAM_UPLOAD_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
            ) * (1 + random.random() * 0.25)
            logger.warning(
                "Instagram upload attempt %d/%d failed for %s (%s) — retrying in %.0fs",
                attempt, INSTAGRAM_UPLOAD_MAX_RETRIES, video_path.name,
                last_error or (outcome.detail if outcome else "unknown error"), delay,
            )
            time.sleep(delay)

    if outcome is None:
        logger.error(
            "Instagram upload raised unexpectedly for %s on every attempt: %s", video_path.name, last_error,
        )
        upload_ledger.mark_failed(content_hash, "instagram", detail=last_error or "unknown error")
        return InstagramOutcome(attempted=True, success=False, detail=last_error or "unknown error")

    if outcome.success and outcome.confirmed:
        upload_ledger.mark_done(content_hash, "instagram", title=title)
    elif outcome.success:
        # Clicked without raising, but Instagram never showed a confirming signal — the same
        # "did it actually go through?" ambiguity TikTok has always had. Left "pending" rather
        # than resolved either way (see upload_ledger.mark_unresolved()'s own docstring): a
        # caller that immediately retries this exact clip is blocked until
        # PENDING_STALE_MINUTES passes, instead of risking a real duplicate Reel post over an
        # unconfirmed signal that may well have actually succeeded.
        upload_ledger.mark_unresolved(content_hash, "instagram", detail="clicked but unconfirmed")
    else:
        upload_ledger.mark_failed(content_hash, "instagram", detail="upload_video returned success=False")

    return InstagramOutcome(attempted=True, success=outcome.success, confirmed=outcome.confirmed)


def _upload_to_tiktok(
    video_path: Path, description: str, hashtags: Optional[List[str]], publish: bool, add_background_sound: bool,
) -> tiktok_uploader.UploadOutcome:
    """Wraps tiktok_uploader.try_upload_clip() with the same content-hash ledger protection as
    YouTube/Instagram above (2026-08-21). TikTok's own click-confirmation signal has always
    been the least reliable of the three (see retry_missing_youtube_uploads()'s own docstring,
    and tiktok_uploader.py's C-08/C-09 history), making an automatic unconfirmed-retry here
    the single most likely source of a genuine duplicate post. publish=False stays a complete
    no-op, mirroring tiktok_uploader.try_upload_clip()'s own contract — no ledger interaction
    for a call that never touches the browser at all."""
    if not publish:
        return tiktok_uploader.try_upload_clip(
            video_path, description, hashtags, publish=publish, add_background_sound=add_background_sound,
        )

    content_hash = upload_ledger.compute_content_hash(video_path)
    existing = upload_ledger.get_entry(content_hash, "tiktok")
    if existing and existing.get("status") == "done":
        logger.info(
            "Skipping TikTok upload for %s — already uploaded per the ledger (hash=%s...); "
            "not submitting a duplicate.", video_path.name, content_hash[:12],
        )
        return tiktok_uploader.UploadOutcome(success=True, confirmed=True)

    if not _try_reserve_upload_pacing_slot("tiktok", TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS):
        logger.info(
            "Skipping TikTok upload for %s — within the fleet-wide daily-quota pacing "
            "interval (another upload happened too recently); will retry a later cycle.",
            video_path.name,
        )
        return tiktok_uploader.UploadOutcome(success=False, confirmed=False)

    if not upload_ledger.try_mark_pending(content_hash, "tiktok"):
        logger.warning(
            "Skipping TikTok upload for %s — another attempt for this exact clip (hash=%s...) "
            "is already in flight, was very recently attempted, or its last attempt's publish "
            "status is still unconfirmed and within the cooldown window; not submitting a "
            "duplicate. This clip will be retried automatically once that cooldown passes.",
            video_path.name, content_hash[:12],
        )
        return tiktok_uploader.UploadOutcome(success=False, confirmed=False)

    outcome = tiktok_uploader.try_upload_clip(
        video_path, description, hashtags, publish=publish, add_background_sound=add_background_sound,
    )

    if outcome.success and outcome.confirmed:
        upload_ledger.mark_done(content_hash, "tiktok")
    elif outcome.success:
        # Clicked without raising, but no redirect/success signal was observed — see
        # upload_ledger.mark_unresolved()'s own docstring. Left as "pending" rather than
        # "failed" so an immediate next-cycle retry is blocked (PENDING_STALE_MINUTES cooldown)
        # instead of risking a second real TikTok post over a click that may well have
        # actually gone through.
        upload_ledger.mark_unresolved(content_hash, "tiktok", detail="clicked but unconfirmed")
    else:
        upload_ledger.mark_failed(content_hash, "tiktok", detail="upload returned success=False")

    return outcome


def upload_clip_everywhere(
    video_path: Path,
    title: str,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
    add_background_sound: bool = True,
    instagram_enabled: bool = False,
) -> MultiPlatformOutcome:
    """Uploads one rendered clip to TikTok, then — after a randomized pacing delay — to
    YouTube Shorts, then — after another pacing delay, only if instagram_enabled — to
    Instagram Reels. TikTok goes first since every existing caller already depends on its
    outcome (moving the file into uploaded_clips/, the metadata sidecar); each delay sits
    BEFORE the next platform so the very first upload of a cycle never waits on nothing.
    instagram_enabled defaults to False — see _upload_to_instagram()'s own docstring for why
    this is a separate, explicit per-streamer opt-in rather than firing automatically.

    Duplicate-upload protection (2026-08-21) now lives one layer down, inside
    _upload_to_tiktok()/_upload_to_youtube()/_upload_to_instagram() themselves, via
    upload_ledger.py's content-hash ledger — every call to this function is safe to repeat for
    the exact same clip (a retry after a crash, a supervisor restart, a caller that doesn't
    know whether an earlier attempt already succeeded) without risking a real duplicate post,
    because each platform's own already-succeeded/in-flight state is checked before any
    network call is made, not passed in by the caller. See upload_ledger.py's own module
    docstring for the two real production incidents this replaced/closes (an earlier, narrower
    fix here — skip_youtube/skip_instagram params reading a per-clip output/ sidecar,
    commit 0aa31b1 — is now redundant and has been removed in favor of this global mechanism)."""
    tiktok_outcome = _upload_to_tiktok(video_path, description, hashtags, publish, add_background_sound)

    if publish:
        delay = random.uniform(UPLOAD_DELAY_MIN_SECONDS, UPLOAD_DELAY_MAX_SECONDS)
        logger.info("Waiting %.0fs before the YouTube upload (rate-limit/pacing buffer)", delay)
        time.sleep(delay)
    youtube_outcome = _upload_to_youtube(video_path, title, description, hashtags, publish)

    if publish and instagram_enabled:
        delay = random.uniform(UPLOAD_DELAY_MIN_SECONDS, UPLOAD_DELAY_MAX_SECONDS)
        logger.info("Waiting %.0fs before the Instagram upload (rate-limit/pacing buffer)", delay)
        time.sleep(delay)
    instagram_outcome = _upload_to_instagram(video_path, title, description, hashtags, publish, instagram_enabled)

    return MultiPlatformOutcome(tiktok=tiktok_outcome, youtube=youtube_outcome, instagram=instagram_outcome)
