"""Upload step: push rendered clips to YouTube as private Shorts, using AI-generated titles."""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Optional

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
