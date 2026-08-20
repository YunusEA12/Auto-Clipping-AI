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

import subprocess
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import analyze
import auto_pilot
import ingest
import notify
import orchestrator
import process as process_module
import process_supervisor
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


def list_agent_state_paths(root: Path = None) -> List[Path]:
    """Every agent-state file currently on disk: the legacy shared AGENT_STATE_PATH (a
    manual/standalone auto_pilot.py run with no --streamer-name) plus one
    agent_state_<slug>.json per streamer orchestrator.py is running concurrently. Multiple
    streamers used to all write the single shared file, corrupting each other's dashboard
    state (see H-14 in the audit) — each now gets its own, so app.py just needs to read all
    of them instead of assuming exactly one exists. `root` defaults to the project's own
    directory (`.`), overridable for tests."""
    if root is None:
        root = Path(".")
    return sorted(root.glob("agent_state*.json"))


def purge_stale_agent_states() -> List[str]:
    """Self-heal on dashboard load (2026-08-19): removes orphaned/stale agent_state*.json
    files (see streamers.purge_stale_agent_states()'s own docstring for the full story —
    found live as a "papaplatte" entry showing twice, one copy 9+ hours stale) so a page
    reload fixes the display even before process_supervisor.py's own periodic purge gets to
    it. Returns the filenames actually deleted."""
    return streamers_module.purge_stale_agent_states()


# --- Fleet Start/Stop (2026-08-19) ----------------------------------------------------------
# app.py's "Start/Stop Fleet" button and process_supervisor.py's poll loop, decoupled through
# fleet_control.json (see process_supervisor.py's own read_fleet_target_state/
# write_fleet_target_state docstrings for the file's shape and fail-safe-to-running default).
# app.py is the only writer, process_supervisor.py the only reader — no lock needed.
FLEET_STATE_RUNNING = process_supervisor.FLEET_STATE_RUNNING
FLEET_STATE_PAUSED = process_supervisor.FLEET_STATE_PAUSED


def read_fleet_target_state() -> str:
    return process_supervisor.read_fleet_target_state()


def set_fleet_target_state(target_state: str) -> None:
    process_supervisor.write_fleet_target_state(target_state)


# --- Remote deployment control (2026-08-21) --------------------------------------------------
# Lets the VPS deployment (see SETUP_SERVER.md) pull the latest code and restart itself from
# the dashboard, instead of needing an SSH session every time. Deliberately named after the
# exact systemd unit names deploy/*.service installs — this only works on that specific
# deployment, never on a local Windows dev checkout (see is_systemd_deployment()).
SUPERVISOR_SYSTEMD_UNIT = "auto-clipping-supervisor"
DASHBOARD_SYSTEMD_UNIT = "auto-clipping-dashboard"
_SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")


def is_systemd_deployment() -> bool:
    """Whether this process is running under the systemd units SETUP_SERVER.md installs —
    used to hide the Deployment controls entirely on a local Windows/manual checkout, where
    `sudo systemctl restart ...` has nothing to act on (no `sudo`, no systemd, no such unit)."""
    return (_SYSTEMD_UNIT_DIR / f"{SUPERVISOR_SYSTEMD_UNIT}.service").exists()


def git_pull_and_restart_supervisor(cwd: Optional[Path] = None) -> Tuple[bool, str]:
    """Runs `git pull` then restarts ONLY the supervisor's systemd unit — safe to call from
    inside the dashboard's own request handler, since it never touches the dashboard's own
    process. Requires a narrowly-scoped passwordless sudo rule for exactly this systemctl
    command (see deploy/sudoers-auto-clipping, installed by SETUP_SERVER.md) — the dashboard
    itself runs as the unprivileged `autoclip` user, same as every other process in this
    deployment.

    Returns (success, detail) rather than raising: the caller (app.py) always has something
    concrete to show the user — the actual git/systemctl output — regardless of which step
    failed."""
    try:
        pull = subprocess.run(
            ["git", "pull"], capture_output=True, text=True, timeout=60, cwd=cwd,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"git pull konnte nicht ausgeführt werden: {e}"
    pull_output = (pull.stdout or "") + (pull.stderr or "")
    if pull.returncode != 0:
        return False, f"git pull fehlgeschlagen:\n{pull_output}"

    try:
        restart = subprocess.run(
            ["sudo", "systemctl", "restart", SUPERVISOR_SYSTEMD_UNIT],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"{pull_output}\n\nsystemctl restart konnte nicht ausgeführt werden: {e}"
    if restart.returncode != 0:
        return False, f"{pull_output}\n\nsystemctl restart fehlgeschlagen:\n{restart.stderr}"

    return True, f"{pull_output}\n\nSupervisor ({SUPERVISOR_SYSTEMD_UNIT}) neu gestartet."


def restart_dashboard_service() -> None:
    """Restarts the dashboard's OWN systemd unit — deliberately fire-and-forget
    (subprocess.Popen, not run()): this call is made from inside the very process about to be
    killed, so nothing after it is guaranteed to execute once systemctl sends the signal. The
    browser tab shows a dropped connection for a few seconds until the new process comes up;
    reloading the page reconnects to it."""
    subprocess.Popen(
        ["sudo", "systemctl", "restart", DASHBOARD_SYSTEMD_UNIT],
        start_new_session=True,
    )


# --- Rendering constants ---------------------------------------------------------------
LAYOUT_AUTO = process_module.LAYOUT_AUTO
LAYOUT_SPLIT_SCREEN = process_module.LAYOUT_SPLIT_SCREEN
LAYOUT_BLUR_BACKGROUND = process_module.LAYOUT_BLUR_BACKGROUND
LAYOUT_FULL_CAM = process_module.LAYOUT_FULL_CAM
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


def add_streamer(
    name: str, url: str, profile: str = "", auto_upload: bool = False, publish: bool = False
) -> None:
    streamers_module.add_streamer(name, url, profile=profile, auto_upload=auto_upload, publish=publish)


def update_streamer(
    name: str,
    url: Optional[str] = None,
    profile: Optional[str] = None,
    auto_upload: Optional[bool] = None,
    publish: Optional[bool] = None,
) -> bool:
    return streamers_module.update_streamer(name, url, profile, auto_upload, publish)


def remove_streamer(name: str) -> bool:
    return streamers_module.remove_streamer(name)


def agent_live_status_by_slug(orchestrator_state: Optional[dict]) -> Dict[str, Dict[str, bool]]:
    """slug -> {"live": bool, "recording": bool} for every configured streamer, from
    orchestrator_state.json's real-time ground truth (the same source the "Konfigurierte
    Streamer" section already trusts) — lets the sidebar's Agent Status cards cross-check a
    state file's own staleness against whether the streamer is actually still live/recording
    right now, instead of judging "online vs. offline" from file staleness alone.

    Found live, 2026-08-20: right after process_supervisor.py restarted, several streamers'
    agent_state_<slug>.json files were still showing a 21+ hour old leftover from before a
    long gap (auto_pilot.py hadn't written its first update since restarting yet — a full
    record+transcribe+analyze cycle can easily take longer than the dashboard's 5-minute
    staleness threshold), so the sidebar wrongly showed "⚠️ Offline" for streamers the main
    content area correctly showed as 🔴 LIVE and recording, right next to each other."""
    result: Dict[str, Dict[str, bool]] = {}
    orchestrator_streamers = (orchestrator_state or {}).get("streamers", {})
    for entry in streamers_module.load_streamers():
        slug = streamers_module._slugify(entry["name"])
        info = orchestrator_streamers.get(entry["name"], {})
        result[slug] = {"live": bool(info.get("live")), "recording": bool(info.get("recording"))}
    return result


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
