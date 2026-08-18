"""Stable interface boundary between app.py (the Streamlit dashboard) and the pipeline
modules it needs (analyze, auto_pilot, ingest, notify, orchestrator, process, profiles,
streamers, tiktok_uploader, train_loop, transcribe, upload).

Before this existed, app.py imported and called into all twelve of those modules directly,
with no boundary between the dashboard and their internals — a signature change in any one
of them was a silent breakage risk for the UI, discoverable only at runtime via a Streamlit
traceback (see M-06 in the audit). Every function app.py actually uses is re-exposed here
with an explicit, fixed signature matching how app.py calls it; if an underlying module's
real signature ever changes, this is the one file that needs updating to match, not every
call site scattered across a 680-line dashboard.

This intentionally does not wrap auto_pilot.py, orchestrator.py, or metrics_tracker.py's
actual behavior — app.py never calls into those at runtime, it only reads the state files
they write (AGENT_STATE_PATH, ORCHESTRATOR_STATE_PATH), which are plain paths re-exported
below, not functions to wrap.
"""

from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import analyze
import auto_pilot
import ingest
import notify
import orchestrator
import process as process_module
import profiles
import streamers as streamers_module
import tiktok_uploader
import train_loop
import transcribe
import upload as upload_module

# --- Paths the dashboard reads directly (written by the background processes) -------------
AGENT_STATE_PATH = auto_pilot.AGENT_STATE_PATH
AI_GUIDELINES_PATH = analyze.AI_GUIDELINES_PATH
ORCHESTRATOR_STATE_PATH = orchestrator.ORCHESTRATOR_STATE_PATH
VIRAL_MEMORY_PATH = train_loop.VIRAL_MEMORY_PATH

# --- Rendering constants ---------------------------------------------------------------
LAYOUT_AUTO = process_module.LAYOUT_AUTO
LAYOUT_SPLIT_SCREEN = process_module.LAYOUT_SPLIT_SCREEN
LAYOUT_BLUR_BACKGROUND = process_module.LAYOUT_BLUR_BACKGROUND
HIGHLIGHT_COLORS = process_module.HIGHLIGHT_COLORS
DEFAULT_HASHTAGS = tiktok_uploader.DEFAULT_HASHTAGS


# --- TikTok -------------------------------------------------------------------------------

def tiktok_cookies_status() -> Tuple[bool, str]:
    return tiktok_uploader.cookies_status()


def upload_clip_to_tiktok(
    video_path: Path, description: str, hashtags: Optional[List[str]] = None, publish: bool = False
) -> tiktok_uploader.UploadOutcome:
    return tiktok_uploader.try_upload_clip(video_path, description, hashtags, publish=publish)


# --- Streamer profiles ("Streamer-Mitarbeiter") --------------------------------------------

def list_profiles() -> List[str]:
    return profiles.list_profiles()


def load_profile(name: str) -> dict:
    return profiles.load_profile(name).model_dump()


# --- Streamer fleet (streamers.json) --------------------------------------------------------

def load_streamers() -> List[dict]:
    return streamers_module.load_streamers()


def add_streamer(name: str, url: str, profile: str = "", auto_upload: bool = False) -> None:
    streamers_module.add_streamer(name, url, profile=profile, auto_upload=auto_upload)


def remove_streamer(name: str) -> bool:
    return streamers_module.remove_streamer(name)


# --- Critic memory (ai_guidelines.txt) -------------------------------------------------------

def parse_guidelines_file(content: str) -> Dict[str, List[str]]:
    return train_loop.parse_guidelines_file(content)


# --- Human feedback (feedback.json) ----------------------------------------------------------

def save_feedback(clip_title: str, feedback_text: str) -> None:
    analyze.save_feedback(clip_title, feedback_text)


# --- Ingestion / transcription / analysis (Manual Mode pipeline) ---------------------------

def download_from_url(url: str) -> Path:
    return ingest.download_from_url(url)


def extract_audio(video_path: Path) -> Path:
    return ingest.extract_audio(video_path)


def transcribe_audio(wav_path: Path) -> Path:
    return transcribe.transcribe(wav_path)


def analyze_transcript(
    transcription_path: Path, audio_path: Optional[Path] = None, profile: Optional[dict] = None
) -> Path:
    return analyze.analyze(transcription_path, audio_path=audio_path, profile=profile)


def load_transcript(transcription_path: Path) -> dict:
    return analyze.load_transcript(transcription_path)


def process_clips_iter(
    source_video: Optional[Path] = None,
    layout: str = LAYOUT_SPLIT_SCREEN,
    video_format: str = process_module.DEFAULT_FORMAT,
    highlight_color: str = process_module.DEFAULT_HIGHLIGHT_COLOR,
    transcript: Optional[dict] = None,
) -> Iterator[Tuple[int, int, dict, Path]]:
    return process_module.process_clips_iter(
        source_video, layout=layout, video_format=video_format, highlight_color=highlight_color,
        transcript=transcript,
    )


# --- Notifications & YouTube upload (Manual Mode pipeline) ---------------------------------

def send_notification(
    title: str, energy: int, filepath: Path, upload_status: str,
    description: str = "", hashtags: Optional[List[str]] = None,
) -> bool:
    return notify.send_notification(
        title=title, energy=energy, filepath=filepath, upload_status=upload_status,
        description=description, hashtags=hashtags,
    )


def upload_all_to_youtube() -> list:
    return upload_module.upload_all()
