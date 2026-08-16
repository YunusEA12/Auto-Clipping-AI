"""TikTok upload step: publish rendered clips via the official TikTok Content Posting API."""

import argparse
import json
import logging
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLIENT_CONFIG_PATH = Path("tiktok_client_secret.json")
TOKEN_PATH = Path("tiktok_token.json")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
# Korrigierter Endpunkt laut offizieller TikTok-Dokumentation für Video-Uploads
INIT_UPLOAD_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"

SCOPE = "video.upload"
REDIRECT_URI = "http://localhost:8921/callback"
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB

DEFAULT_HASHTAGS = "#fyp #viral #shorts #gaming"


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        self.server.auth_code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Authorization complete. You can close this tab.</body></html>")

    def log_message(self, format, *args):
        pass


def _load_client_config() -> dict:
    """Load {client_key, client_secret} from tiktok_client_secret.json, falling back to
    the TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET environment variables (.env) if the file
    doesn't exist — useful for headless/CI-style setups without a checked-in secrets file."""
    if CLIENT_CONFIG_PATH.exists():
        with open(CLIENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    load_dotenv()
    client_key = os.environ.get("TIKTOK_CLIENT_KEY")
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET")
    if client_key and client_secret:
        return {"client_key": client_key, "client_secret": client_secret}

    raise FileNotFoundError(
        f"Missing TikTok app credentials: {CLIENT_CONFIG_PATH} not found, and "
        "TIKTOK_CLIENT_KEY/TIKTOK_CLIENT_SECRET are not set in .env. "
        'Create a TikTok Developer app with the Content Posting API and either save '
        '{"client_key": "...", "client_secret": "..."} to tiktok_client_secret.json, '
        "or set those two variables in .env."
    )


def _run_oauth_flow(client_key: str, client_secret: str) -> dict:
    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "state": "auto-clipping-ai",
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"
    logger.info("Opening browser for TikTok authorization")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8921), _OAuthCallbackHandler)
    server.auth_code = None
    try:
        while server.auth_code is None:
            server.handle_request()
    finally:
        server.server_close()

    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": server.auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    if "access_token" not in token_data:
        raise RuntimeError(f"TikTok OAuth failed: {token_data}")

    TOKEN_PATH.write_text(json.dumps(token_data), encoding="utf-8")
    logger.info("Saved TikTok OAuth token to %s", TOKEN_PATH)
    return token_data


def _refresh_token(client_key: str, client_secret: str, refresh_token: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    token_data = response.json()
    if "access_token" not in token_data:
        raise RuntimeError(f"TikTok token refresh failed: {token_data}")

    TOKEN_PATH.write_text(json.dumps(token_data), encoding="utf-8")
    return token_data


def _get_access_token(interactive: bool = True) -> str:
    """`interactive=False` (used by automated pipelines) never opens a browser: if there's
    no valid cached/refreshable token, it raises instead of blocking on human input."""
    config = _load_client_config()
    client_key = config["client_key"]
    client_secret = config["client_secret"]

    if TOKEN_PATH.exists():
        try:
            stored = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
            token_data = _refresh_token(client_key, client_secret, stored["refresh_token"])
            return token_data["access_token"]
        except (requests.RequestException, KeyError, RuntimeError, json.JSONDecodeError) as e:
            logger.warning("Could not refresh TikTok token: %s", e)
            if not interactive:
                raise RuntimeError(
                    "TikTok token missing or expired, and interactive re-authorization is "
                    "disabled. Run `python upload_tiktok.py <video> --caption ...` once "
                    "manually to (re-)authorize, then retry."
                ) from e

    elif not interactive:
        raise RuntimeError(
            "No cached TikTok token, and interactive re-authorization is disabled. Run "
            "`python upload_tiktok.py <video> --caption ...` once manually to authorize first."
        )

    logger.info("Starting interactive TikTok authorization...")
    token_data = _run_oauth_flow(client_key, client_secret)
    return token_data["access_token"]


def build_caption(title: str, hashtags: str = DEFAULT_HASHTAGS) -> str:
    return f"{title} {hashtags}".strip()


def upload_to_tiktok(video_path: Path, caption: str, interactive: bool = True) -> str:
    """Upload a rendered clip to TikTok as an inbox draft via the Content Posting API.

    Note: the inbox init endpoint has no `post_info`/caption field — TikTok only lets the
    creator set the caption manually in-app when they choose to post from their inbox.
    `caption` is accepted (and still built via `build_caption()`) so callers/CLI usage
    stay stable and the text is ready to copy-paste, but it is not transmitted here.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    access_token = _get_access_token(interactive)
    video_size = video_path.stat().st_size

    logger.info(
        "Initializing TikTok inbox upload for %s (%.1f MB); suggested caption: %r",
        video_path.name, video_size / 1_000_000, caption,
    )
    try:
        init_response = requests.post(
            INIT_UPLOAD_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": min(video_size, CHUNK_SIZE),
                    "total_chunk_count": max(1, -(-video_size // CHUNK_SIZE)),
                }
            },
            timeout=30,
        )
        init_response.raise_for_status()
    except requests.RequestException as e:
        logger.error("TikTok upload init failed for %s: %s", video_path, e)
        raise

    init_data = init_response.json()
    error = init_data.get("error", {})
    if error and error.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok upload init failed: {error}")

    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]

    logger.info("Uploading video bytes to TikTok (publish_id=%s)", publish_id)
    try:
        with open(video_path, "rb") as f:
            video_bytes = f.read()

        upload_response = requests.put(
            upload_url,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
            },
            data=video_bytes,
            timeout=300,
        )
        upload_response.raise_for_status()
    except requests.RequestException as e:
        logger.error("TikTok video upload failed for %s: %s", video_path, e)
        raise

    logger.info("TikTok upload complete (sent to inbox) for %s: publish_id=%s", video_path.name, publish_id)
    return publish_id


def try_upload_to_tiktok(video_path: Path, caption: str) -> Optional[str]:
    """Non-raising wrapper for automated/unattended pipelines (e.g. stream_watcher.py).

    Catches every failure mode — missing tiktok_client_secret.json, missing/expired token,
    network errors, a bad API response — logs it, and returns None instead of propagating,
    so one failed upload never crashes an unattended watch loop. Never opens a browser for
    interactive OAuth (no human is present to click through it); if no valid token is
    already cached, it fails cleanly instead.
    """
    try:
        return upload_to_tiktok(video_path, caption, interactive=False)
    except Exception as e:
        logger.error("TikTok upload failed for %s, continuing without upload: %s", video_path, e)
        return None


def main():
    parser = argparse.ArgumentParser(description="Upload a single clip to TikTok.")
    parser.add_argument("video", type=Path, help="Path to the rendered .mp4 clip")
    parser.add_argument("--caption", required=True, help="Caption/title to post with")
    args = parser.parse_args()

    upload_to_tiktok(args.video, args.caption)


if __name__ == "__main__":
    main()