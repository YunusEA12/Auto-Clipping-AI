"""Upload step: push rendered clips to YouTube as private Shorts, using AI-generated titles."""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
OUTPUT_DIR = Path("output")
CLIENT_SECRET_PATH = Path("client_secret.json")
TOKEN_PATH = Path("token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
DEFAULT_HASHTAGS = "#shorts #gaming"
PRIVACY_STATUS = "private"

# YouTube's own hard limit on video titles (2026-08-20) — used by upload_clip() below, the
# per-clip entry point upload_manager.py calls. upload_video()/upload_all() above already had
# their own title[:100] truncation; this constant just gives that number a name for the new
# function's own (slightly smarter, "leave room for the tag") truncation.
TITLE_MAX_LENGTH = 100
SHORTS_TAG = "#shorts"


def _missing_scopes(creds: Credentials) -> set:
    """Credentials.from_authorized_user_file(path, SCOPES) reads the ACTUAL granted scopes
    from the stored token file itself, not from the SCOPES passed in — that parameter only
    matters for a brand-new interactive consent request. A stored token issued before SCOPES
    grew a new entry keeps whatever it was originally granted forever; refresh() renews the
    access token but can never add a scope that was never consented to. Without this check,
    that mismatch only ever surfaces as a 403 from whatever API call needed the missing scope
    — exactly what happened with youtube.readonly before this was added (found in review,
    2026-08-21, after live-debugging that exact 403 earlier this session)."""
    return set(SCOPES) - set(creds.scopes or [])


def get_authenticated_service():
    if not CLIENT_SECRET_PATH.exists():
        raise FileNotFoundError(
            f"Missing OAuth client secret: {CLIENT_SECRET_PATH}. "
            "Download it from Google Cloud Console (YouTube Data API v3 credentials)."
        )

    creds: Optional[Credentials] = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired YouTube OAuth token")
            creds.refresh(Request())
        else:
            logger.info("Starting OAuth2 flow for YouTube upload authorization")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Saved YouTube OAuth token to %s", TOKEN_PATH)

    missing = _missing_scopes(creds)
    if missing:
        logger.warning(
            "YouTube token in %s is missing scope(s) %s — API calls needing them will 403. "
            "SCOPES was changed after this token was last issued; delete %s and re-run the "
            "interactive OAuth flow to grant the current SCOPES.", TOKEN_PATH, missing, TOKEN_PATH,
        )

    return build("youtube", "v3", credentials=creds)


# YouTube's own limit on how many comma-separated ids videos.list() accepts in one call.
STATS_BATCH_SIZE = 50


def get_stats_service():
    """Read-only YouTube Data API client for metrics_tracker.py's stats-fetch (2026-08-21) —
    deliberately separate from get_authenticated_service() above: that function falls into
    flow.run_local_server()'s interactive OAuth flow when there's no valid token, which would
    hang forever on a headless VPS with no browser to complete it in. This one only ever does
    a non-interactive refresh and returns None (never raises) when there's nothing usable to
    refresh — same "no session material -> return None, let the caller skip this cycle"
    contract as tiktok_uploader.load_cookies()."""
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except (ValueError, OSError) as e:
        logger.warning("Could not read %s: %s", TOKEN_PATH, e)
        return None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning("Could not refresh YouTube OAuth token: %s", e)
                return None
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        else:
            logger.warning(
                "YouTube OAuth token in %s is invalid and has no refresh_token — re-run a "
                "real upload once to re-authenticate interactively.", TOKEN_PATH,
            )
            return None

    missing = _missing_scopes(creds)
    if missing:
        # Same "nothing usable -> None, let the caller skip this cycle" contract as a missing
        # token above — a scope-insufficient client isn't usable for what this function's
        # callers need either, and returning it anyway would just trade a clear log line here
        # for a 403 several calls downstream (found in review, 2026-08-21).
        logger.warning(
            "YouTube token in %s is missing scope(s) %s — treating as unusable. SCOPES was "
            "changed after this token was last issued; delete %s and re-run the interactive "
            "OAuth flow to grant the current SCOPES.", TOKEN_PATH, missing, TOKEN_PATH,
        )
        return None

    return build("youtube", "v3", credentials=creds)


def _coerce_stat_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def fetch_video_stats(youtube, video_ids: List[str]) -> Dict[str, dict]:
    """{video_id: {"views": int | None, "likes": int | None}} for each id YouTube actually
    returns a row for. A deleted video or one videos.list otherwise can't return simply drops
    out of the response rather than erroring the whole batch. likeCount can be legitimately
    absent (a creator can hide their like count) — that comes back as None, not 0, same
    never-guess-on-ambiguity principle as metrics_tracker._coerce_int(). Never raises — a
    broken batch just logs and is skipped, mirroring metrics_tracker.fetch_content_list()'s
    "a broken fetch must degrade gracefully, not crash the loop" contract."""
    stats: Dict[str, dict] = {}
    unique_ids = list(dict.fromkeys(video_ids))
    for i in range(0, len(unique_ids), STATS_BATCH_SIZE):
        batch = unique_ids[i:i + STATS_BATCH_SIZE]
        try:
            response = youtube.videos().list(part="statistics", id=",".join(batch)).execute()
        except HttpError as e:
            logger.error("Could not fetch YouTube stats for a batch of %d video(s): %s", len(batch), e)
            continue
        for item in response.get("items", []):
            video_id = item.get("id")
            if not video_id:
                continue
            statistics = item.get("statistics", {})
            stats[video_id] = {
                "views": _coerce_stat_int(statistics.get("viewCount")),
                "likes": _coerce_stat_int(statistics.get("likeCount")),
            }
    return stats


def find_latest_clips_path() -> Optional[Path]:
    """Clips are now named per source video (temp/<video>_clips.json); use the most
    recently written one since upload_all() has no per-video context of its own."""
    candidates = sorted(TEMP_DIR.glob("*_clips.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def load_clips_metadata() -> dict:
    clips_path = find_latest_clips_path()
    if clips_path is None:
        raise FileNotFoundError(f"No *_clips.json file found in {TEMP_DIR}")
    with open(clips_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {i: clip for i, clip in enumerate(data.get("clips", []), start=1)}


def parse_clip_index(video_path: Path) -> Optional[int]:
    match = re.match(r"clip_(\d+)_", video_path.name)
    return int(match.group(1)) if match else None


def upload_video(youtube, video_path: Path, clip: Optional[dict]) -> str:
    title = clip["title"] if clip else video_path.stem
    hook = clip.get("hook_explanation", "") if clip else ""

    description = hook
    if description:
        description += "\n\n"
    description += DEFAULT_HASHTAGS

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["shorts", "gaming"],
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    logger.info("Uploading %s as '%s' (privacy=%s)", video_path, title, PRIVACY_STATUS)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress for %s: %d%%", video_path.name, int(status.progress() * 100))

    video_id = response["id"]
    logger.info("Uploaded %s -> https://youtu.be/%s", video_path.name, video_id)
    return video_id


def _append_shorts_tag(text: str, max_length: Optional[int] = None) -> str:
    """Appends SHORTS_TAG unless it's already present (case-insensitive) — needed to reliably
    hit the Shorts algorithm/surface. When `max_length` is given (the title, which has
    YouTube's 100-char hard limit; the description has none), the base text is trimmed first
    so the combined result never exceeds it, rather than truncating AFTER appending and
    risking cutting the tag itself off."""
    if SHORTS_TAG.lower() in text.lower():
        return text if max_length is None else text[:max_length]
    combined = f"{text.rstrip()} {SHORTS_TAG}"
    if max_length is None or len(combined) <= max_length:
        return combined
    available = max_length - len(SHORTS_TAG) - 1  # -1 for the space before the tag
    return f"{text[:available].rstrip()} {SHORTS_TAG}"


def _find_recent_upload_by_title(youtube, title: str, lookback: int = 10) -> Optional[str]:
    """Checks the channel's own uploads playlist for a video whose title exactly matches —
    used to verify whether an upload that raised locally actually succeeded server-side
    before the caller assumes it failed and retries, which would otherwise risk posting the
    exact same clip a second time (2026-08-21, account-owner request: "verify the remote
    state before retrying so the exact same clip is never posted twice").

    YouTube's resumable upload can fail AFTER the video bytes are fully received but before
    the response confirming the new video's id reaches this process (a network blip on the
    final chunk, a timeout waiting for the acknowledgement) — from here, that is
    indistinguishable from a genuine failure where nothing was ever created. Only the channel
    itself can say which one actually happened.

    Only checks the most recent `lookback` uploads (cheap: two small API list calls, not a
    video insert) — a real duplicate risk is always near the front of the channel's own
    upload history, never buried in it. Best-effort: any failure here (including no channel
    found) returns None rather than raising, so a verification problem never masks the
    original error the caller is already handling."""
    try:
        channels_response = youtube.channels().list(mine=True, part="contentDetails").execute()
        items = channels_response.get("items", [])
        if not items:
            return None
        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        playlist_response = youtube.playlistItems().list(
            playlistId=uploads_playlist_id, part="snippet", maxResults=lookback,
        ).execute()
        for item in playlist_response.get("items", []):
            snippet = item.get("snippet", {})
            if snippet.get("title") == title:
                return snippet.get("resourceId", {}).get("videoId")
    except Exception as e:
        logger.warning("Could not verify remote YouTube upload state for title %r: %s", title, e)
    return None


def upload_clip(
    video_path: Path,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: str = "public",
) -> str:
    """Per-clip YouTube Shorts upload — the entry point upload_manager.py calls, one already-
    rendered clip at a time, mirroring tiktok_uploader.try_upload_clip()'s per-clip interface.
    Distinct from upload_video()/upload_all() above (that pair is the manual/exploratory batch
    CLI, `python upload.py`, which still defaults to private — deliberately left unchanged):
    this is the live, automated multi-platform pipeline path, so it defaults to public.

    Raises on failure rather than swallowing it — upload_manager.py is the layer that decides
    a YouTube failure shouldn't crash the whole cycle, the same layering tiktok_uploader.py
    (which can itself raise) plus its caller already use.

    Returns the uploaded video's ID."""
    youtube = get_authenticated_service()

    final_title = _append_shorts_tag(title.strip(), max_length=TITLE_MAX_LENGTH)
    final_description = _append_shorts_tag((description or title).strip())
    # Tags are plain keywords, not hashtags (YouTube API convention) — strip any leading '#'
    # a caller passes through from TikTok-style hashtags (e.g. clip["hashtags"]).
    final_tags = list(dict.fromkeys([t.lstrip("#") for t in (tags or []) if t.strip()] + ["shorts"]))

    body = {
        "snippet": {
            "title": final_title,
            "description": final_description,
            "tags": final_tags,
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    logger.info("Uploading %s to YouTube as '%s' (privacy=%s)", video_path.name, final_title, privacy_status)
    try:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("YouTube upload progress for %s: %d%%", video_path.name, int(status.progress() * 100))
    except Exception as e:
        # The upload call itself raised — see _find_recent_upload_by_title()'s own docstring
        # for why that alone doesn't mean nothing was created. Check before letting the
        # caller's own retry logic (upload_manager.py) run again on this exact clip.
        existing_id = _find_recent_upload_by_title(youtube, final_title)
        if existing_id:
            logger.warning(
                "Upload request for %s raised (%s), but a video titled %r already exists on "
                "the channel (id=%s) — treating this as the real success it apparently was, "
                "not re-raising for a retry that would post it a second time.",
                video_path.name, e, final_title, existing_id,
            )
            return existing_id
        raise

    video_id = response["id"]
    logger.info("Uploaded %s to YouTube -> https://youtu.be/%s", video_path.name, video_id)
    return video_id


def upload_all() -> list:
    if not OUTPUT_DIR.exists():
        raise FileNotFoundError(f"Output directory not found: {OUTPUT_DIR}")

    video_files = sorted(OUTPUT_DIR.glob("*.mp4"))
    if not video_files:
        raise FileNotFoundError(f"No .mp4 files found in {OUTPUT_DIR}")

    clips_by_index = load_clips_metadata()
    youtube = get_authenticated_service()

    uploaded_ids = []
    for video_path in video_files:
        index = parse_clip_index(video_path)
        clip = clips_by_index.get(index) if index is not None else None
        if clip is None:
            logger.warning("No clip metadata found for %s, falling back to filename as title", video_path.name)

        try:
            video_id = upload_video(youtube, video_path, clip)
            uploaded_ids.append(video_id)
        except HttpError as e:
            if e.resp.status == 403 and "quota" in str(e).lower():
                logger.error("YouTube API quota exceeded, stopping uploads: %s", e)
                raise
            logger.error("Upload failed for %s: %s", video_path, e)
            continue

    logger.info("Upload complete: %d/%d videos uploaded", len(uploaded_ids), len(video_files))
    return uploaded_ids


def main():
    parser = argparse.ArgumentParser(description="Upload rendered clips to YouTube as private Shorts.")
    parser.parse_args()
    upload_all()


if __name__ == "__main__":
    main()
