"""Fleet Manager / Orchestrator: watches streamers.json around the clock and starts/stops
one auto_pilot.py subprocess per streamer based on whether they're actually live.

Every poll cycle (default every 90s, configurable via --poll-interval):
  1. Re-read streamers.json — streamers can be added/removed/edited while this runs, no
     restart needed.
  2. For each configured streamer, check liveness via `streamlink <url> --json` (works
     across every platform streamlink supports — Twitch, YouTube, Kick, ... — without
     needing a per-platform API token).
  3. LIVE and not yet tracked  -> start `python auto_pilot.py --url <url> --live [--profile
     <profile>] [--auto-upload]` as a detached subprocess (subprocess.Popen) and track it.
  4. OFFLINE and still tracked -> terminate that subprocess and stop tracking it. (A tracked
     subprocess that already exited on its own — e.g. it crashed — is also cleaned up here.)
  5. Write the full picture (which streamers are live, which are actively being recorded,
     PIDs, when each recording started) to orchestrator_state.json, so app.py's "👥 Streamer
     Verwaltung & Fleet" tab can show it without any direct connection to this process.

This script only orchestrates OS-level subprocesses — it never imports auto_pilot.py as a
module (that would run its own `while True` loop inside this process instead of as an
independent, individually restartable process per streamer).

Usage:
    python orchestrator.py
    python orchestrator.py --poll-interval 60
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import streamers as streamers_module
import stream_watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 90
LIVENESS_CHECK_TIMEOUT_SECONDS = 30
STOP_GRACE_PERIOD_SECONDS = 15

# Telemetry file app.py's Fleet tab polls — analogous to auto_pilot.py's agent_state.json,
# just one level up (which streamers are live/recording, not what one agent is doing).
ORCHESTRATOR_STATE_PATH = Path("orchestrator_state.json")


def is_stream_live(url: str, timeout: int = LIVENESS_CHECK_TIMEOUT_SECONDS) -> bool:
    """Best-effort liveness check via `streamlink <url> --json`. Never raises — a flaky
    check must never crash the orchestrator loop, it just reports offline and tries again
    next cycle."""
    try:
        streamlink_path = stream_watcher.resolve_streamlink_path()
    except RuntimeError as e:
        logger.error("streamlink not found, cannot check liveness: %s", e)
        return False

    try:
        result = subprocess.run(
            [streamlink_path, url, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Liveness check failed for %s: %s", url, e)
        return False

    if result.returncode != 0:
        return False

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False

    return bool(data.get("streams"))


def build_auto_pilot_cmd(entry: dict) -> List[str]:
    cmd = [sys.executable, "auto_pilot.py", "--url", entry["url"], "--live"]
    if entry.get("profile"):
        cmd += ["--profile", entry["profile"]]
    if entry.get("auto_upload"):
        cmd.append("--auto-upload")
    return cmd


def start_recording(entry: dict) -> subprocess.Popen:
    cmd = build_auto_pilot_cmd(entry)
    logger.info("🔴 '%s' ist live — starte auto_pilot.py: %s", entry["name"], " ".join(cmd))
    return subprocess.Popen(cmd)


def stop_recording(name: str, process: subprocess.Popen) -> None:
    logger.info("⏹️ '%s' ist offline — beende auto_pilot.py (PID %s)", name, process.pid)
    process.terminate()
    try:
        process.wait(timeout=STOP_GRACE_PERIOD_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("'%s' reagierte nicht auf terminate(), erzwinge kill()", name)
        process.kill()
        process.wait(timeout=STOP_GRACE_PERIOD_SECONDS)


def write_orchestrator_state(streamers_status: Dict[str, dict], poll_interval: int, status: str = "running") -> None:
    state = {
        "status": status,
        "last_check": datetime.now(timezone.utc).isoformat(),
        "poll_interval": poll_interval,
        "streamers": streamers_status,
    }
    try:
        ORCHESTRATOR_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write %s: %s", ORCHESTRATOR_STATE_PATH, e)


def run_orchestrator(
    streamers_path: Path = streamers_module.STREAMERS_PATH,
    poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    max_iterations: Optional[int] = None,
) -> None:
    tracked: Dict[str, dict] = {}  # name -> {"process": Popen, "started_at": iso, "entry": dict}

    logger.info(
        "🛰️ Orchestrator gestartet (streamers=%s, poll_interval=%ds)", streamers_path, poll_interval
    )

    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1

            entries = streamers_module.load_streamers(streamers_path)
            entries_by_name = {e["name"]: e for e in entries}

            # A streamer removed from streamers.json entirely stops being watched — clean
            # up its recording immediately rather than waiting for the next offline check.
            for name in list(tracked):
                if name not in entries_by_name:
                    logger.info("'%s' wurde aus %s entfernt — stoppe laufende Aufnahme", name, streamers_path)
                    try:
                        stop_recording(name, tracked[name]["process"])
                    except Exception as e:
                        logger.error("Fehler beim Beenden von '%s': %s", name, e)
                    del tracked[name]

            status_snapshot: Dict[str, dict] = {}
            for entry in entries:
                name = entry["name"]
                try:
                    live = is_stream_live(entry["url"])
                except Exception as e:
                    logger.error("Live-Check für '%s' fehlgeschlagen: %s", name, e)
                    live = False

                current = tracked.get(name)
                if current is not None and current["process"].poll() is not None:
                    logger.warning(
                        "auto_pilot.py für '%s' ist unerwartet beendet (exit=%s)",
                        name, current["process"].returncode,
                    )
                    del tracked[name]
                    current = None

                if live and current is None:
                    try:
                        process = start_recording(entry)
                        tracked[name] = {
                            "process": process,
                            "started_at": datetime.now(timezone.utc).isoformat(),
                        }
                    except Exception as e:
                        logger.error("Konnte auto_pilot.py für '%s' nicht starten: %s", name, e)
                elif not live and current is not None:
                    try:
                        stop_recording(name, current["process"])
                    except Exception as e:
                        logger.error("Konnte auto_pilot.py für '%s' nicht sauber beenden: %s", name, e)
                    tracked.pop(name, None)

                tracked_info = tracked.get(name)
                status_snapshot[name] = {
                    "live": live,
                    "recording": tracked_info is not None,
                    "pid": tracked_info["process"].pid if tracked_info else None,
                    "started_at": tracked_info["started_at"] if tracked_info else None,
                    "url": entry["url"],
                    "profile": entry.get("profile", ""),
                    "auto_upload": bool(entry.get("auto_upload", False)),
                }

            write_orchestrator_state(status_snapshot, poll_interval)

            if max_iterations is None or iteration < max_iterations:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Orchestrator durch Nutzer gestoppt — beende alle laufenden Aufnahmen...")
    finally:
        for name, info in list(tracked.items()):
            try:
                stop_recording(name, info["process"])
            except Exception as e:
                logger.error("Fehler beim Beenden von '%s': %s", name, e)
        write_orchestrator_state({}, poll_interval, status="stopped")


def main():
    parser = argparse.ArgumentParser(
        description="24/7 fleet orchestrator: starts/stops auto_pilot.py per streamer in streamers.json based on live status."
    )
    parser.add_argument(
        "--streamers-path", type=Path, default=streamers_module.STREAMERS_PATH,
        help="Path to the streamer config JSON (default: %(default)s)",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Seconds between live-status checks (default: %(default)s)",
    )
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after N polling cycles (omit to run forever)")
    args = parser.parse_args()

    run_orchestrator(args.streamers_path, args.poll_interval, args.max_iterations)


if __name__ == "__main__":
    main()
