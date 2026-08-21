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

import logging
import random
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

from googleapiclient.errors import HttpError

import tiktok_uploader
import upload as youtube_uploader

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

# Random pacing delay between the TikTok and YouTube upload attempts (2026-08-20, explicit
# account-owner request) — staggers the two platforms' API/network traffic instead of firing
# both back-to-back. Only applied when actually publishing (publish=False makes no real calls
# to either platform, so there is nothing to pace out).
UPLOAD_DELAY_MIN_SECONDS = 30
UPLOAD_DELAY_MAX_SECONDS = 60


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


class MultiPlatformOutcome(NamedTuple):
    tiktok: tiktok_uploader.UploadOutcome
    youtube: YouTubeOutcome

    @property
    def any_success(self) -> bool:
        tiktok_success = self.tiktok.success and self.tiktok.confirmed
        return tiktok_success or self.youtube.success


def _upload_to_youtube(
    video_path: Path, title: str, description: str, tags: Optional[List[str]], publish: bool,
) -> YouTubeOutcome:
    if not publish:
        logger.info("publish=False — skipping YouTube upload for %s (same no-op as TikTok)", video_path.name)
        return YouTubeOutcome(attempted=False, success=False, detail="publish=False, skipped")

    try:
        video_id = youtube_uploader.upload_clip(video_path, title, description, tags)
    except HttpError as e:
        quota_exceeded = e.resp.status == 403 and "quota" in str(e).lower()
        if quota_exceeded:
            logger.error("YouTube API quota exceeded — could not upload %s: %s", video_path.name, e)
        else:
            logger.error("YouTube upload failed for %s: %s", video_path.name, e)
        return YouTubeOutcome(attempted=True, success=False, detail=str(e))
    except Exception as e:
        # Anything else (OAuth/token problem, network failure, a missing client_secret.json)
        # — never let a YouTube-side problem take down the cycle that's also reporting the
        # TikTok result.
        logger.error("YouTube upload raised unexpectedly for %s: %s", video_path.name, e)
        return YouTubeOutcome(attempted=True, success=False, detail=str(e))

    # Background music for YouTube is now mixed directly into the render (process.py's
    # build_audio_filter(), via ffmpeg) before this upload ever runs — dropped the Playwright
    # YouTube Studio automation this step used to call (youtube_studio_uploader.py), which
    # never got past cookie-based login on this VPS (Google's session cookies appear to be
    # IP-bound) and risked the account's OAuth token being flagged for scripted Studio access.
    return YouTubeOutcome(attempted=True, success=True, video_id=video_id)


def upload_clip_everywhere(
    video_path: Path,
    title: str,
    description: str,
    hashtags: Optional[List[str]] = None,
    publish: bool = False,
    add_background_sound: bool = True,
) -> MultiPlatformOutcome:
    """Uploads one rendered clip to TikTok, then — after a randomized pacing delay — to
    YouTube Shorts. TikTok goes first since every existing caller already depends on its
    outcome (moving the file into uploaded_clips/, the metadata sidecar); the delay sits
    before YouTube specifically so the very first upload of a cycle never waits on nothing."""
    tiktok_outcome = tiktok_uploader.try_upload_clip(
        video_path, description, hashtags, publish=publish, add_background_sound=add_background_sound,
    )

    if publish:
        delay = random.uniform(UPLOAD_DELAY_MIN_SECONDS, UPLOAD_DELAY_MAX_SECONDS)
        logger.info("Waiting %.0fs before the YouTube upload (rate-limit/pacing buffer)", delay)
        time.sleep(delay)

    youtube_outcome = _upload_to_youtube(video_path, title, description, hashtags, publish)

    return MultiPlatformOutcome(tiktok=tiktok_outcome, youtube=youtube_outcome)
