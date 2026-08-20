"""Upload step: push rendered clips to YouTube as private Shorts, using AI-generated titles."""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import List, Optional

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

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_HASHTAGS = "#shorts #gaming"
PRIVACY_STATUS = "private"

# YouTube's own hard limit on video titles (2026-08-20) — used by upload_clip() below, the
# per-clip entry point upload_manager.py calls. upload_video()/upload_all() above already had
# their own title[:100] truncation; this constant just gives that number a name for the new
# function's own (slightly smarter, "leave room for the tag") truncation.
TITLE_MAX_LENGTH = 100
SHORTS_TAG = "#shorts"


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

    return build("youtube", "v3", credentials=creds)


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
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("YouTube upload progress for %s: %d%%", video_path.name, int(status.progress() * 100))

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
