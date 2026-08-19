"""Top-level process supervisor: the missing "who watches the watchers" layer.

orchestrator.py restarts auto_pilot.py subprocesses when a streamer goes live (with
crash-loop backoff — see orchestrator._crash_backoff_seconds). But nothing used to restart
orchestrator.py itself if IT died: a closed terminal, an uncaught exception outside its own
try block, a machine reboot, an OOM-kill. Same for metrics_tracker.py. Three independent
long-running processes, zero watchdog-of-watchdog.

This script is the single recommended entry point for running the fleet 24/7: it launches
orchestrator.py and metrics_tracker.py as subprocesses and restarts either one (with the same
exponential-backoff-on-fast-crash logic orchestrator.py itself uses for auto_pilot.py) if it
exits unexpectedly. It does not replace orchestrator.py/metrics_tracker.py or duplicate their
logic — it only supervises the two OS processes.

Named process_supervisor.py, not watchdog.py: an earlier version of this file WAS named
watchdog.py, which silently shadowed the third-party `watchdog` package (a dependency of
Streamlit's file-watcher) for every script run from this project's root — since the current
directory takes precedence on sys.path, `import watchdog` anywhere in this project resolved
to this file instead of the real package, breaking `streamlit run app.py` outright. Caught by
actually launching Streamlit and inspecting its server log, not by reasoning about it — do
the same before ever reusing a name that could plausibly collide with a real package again.

This is still a single point of failure at the very top: if THIS process's host machine goes
down, or this process itself is killed with nothing outside it to restart it, the fleet stops.
Turning it into a true no-single-point-of-failure setup means wiring it into an OS-level
service manager (Windows Task Scheduler "at startup" trigger, or NSSM as a Windows service)
so the OS restarts it automatically after a reboot — that's a one-time, machine-specific setup
step outside what a Python script can safely do on its own (it needs admin rights / GUI
interaction), so it's left as an explicit manual step rather than attempted here.

Usage:
    python process_supervisor.py
    python process_supervisor.py --poll-interval 30
    python process_supervisor.py --skip-metrics-tracker   # e.g. cookies.json isn't set up yet
"""

import argparse
import json
import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import psutil

import atomic_io
import streamers as streamers_module

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 30
CRASH_LOOP_WINDOW_SECONDS = 60
CRASH_BACKOFF_BASE_SECONDS = 30
CRASH_BACKOFF_MAX_SECONDS = 1800
CRASH_BACKOFF_MAX_STREAK = 20

# 2026-08-19: found live — Ctrl+C on this supervisor left ffmpeg/streamlink recording
# processes running in the background indefinitely. Root cause: SupervisedProcess.stop() used
# to call plain Popen.terminate() on orchestrator.py — on Windows that's an unconditional
# TerminateProcess, which kills the target with NO chance for its own Python-level cleanup to
# run at all. orchestrator.py already has correct KeyboardInterrupt-driven cleanup (it stops
# every tracked auto_pilot.py subprocess in its own `finally` block), but that code was simply
# never reached — the hard kill bypassed it entirely, so auto_pilot.py (and in turn whatever
# ffmpeg/streamlink call it happened to be blocked in) was orphaned right along with it.
#
# The fix below is deliberately two-phase and does not depend on any single layer's internal
# cleanup actually running correctly:
#   1. A genuine interrupt (CTRL_BREAK_EVENT on Windows, SIGINT on POSIX — both raise a real,
#      catchable KeyboardInterrupt in the target, unlike terminate()) gives orchestrator.py/
#      metrics_tracker.py a real chance to run their own existing graceful-shutdown code.
#   2. Regardless of whether phase 1 worked at every level, every descendant process (at ANY
#      depth — auto_pilot.py, ffmpeg, streamlink) is snapshotted BEFORE phase 1 runs and
#      force-killed if still alive afterward. This is the actual guarantee against orphans:
#      even if some intermediate script has its own bug, is itself stuck, or a signal simply
#      isn't delivered, nothing from the original snapshot survives this function.
GRACEFUL_STOP_TIMEOUT_SECONDS = 30
FORCE_KILL_WAIT_SECONDS = 5

# 2026-08-19: terminal-based start/stop (Ctrl+C the supervisor, re-run it by hand) was
# frustrating for iterative testing — a simple IPC file lets app.py's dashboard request a
# pause/resume without touching the terminal. app.py is the only writer, this module the only
# reader — a single-writer file needs no lock (contrast streamers.json, which both app.py and
# orchestrator.py write and DOES need one — see streamers._streamers_lock).
FLEET_CONTROL_PATH = Path("fleet_control.json")
FLEET_STATE_RUNNING = "running"
FLEET_STATE_PAUSED = "paused"


def read_fleet_target_state(path: Path = None) -> str:
    """Best-effort, fails safe toward FLEET_STATE_RUNNING: a fleet_control.json that's
    missing (dashboard never opened, or a fresh checkout), corrupt, or holds an unrecognized
    value must never accidentally pause a fleet nobody asked to pause — same
    never-guess-when-ambiguous principle as streamers.purge_stale_agent_states()."""
    if path is None:
        path = FLEET_CONTROL_PATH
    if not path.exists():
        return FLEET_STATE_RUNNING
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return FLEET_STATE_RUNNING
    target = data.get("target_state")
    return target if target in (FLEET_STATE_RUNNING, FLEET_STATE_PAUSED) else FLEET_STATE_RUNNING


def write_fleet_target_state(target_state: str, path: Path = None) -> None:
    """Called from app.py (via dashboard_api.set_fleet_target_state) when the Start/Stop
    Fleet button is clicked. Raises on an invalid value rather than silently writing garbage
    a future read would have to guess about."""
    if target_state not in (FLEET_STATE_RUNNING, FLEET_STATE_PAUSED):
        raise ValueError(f"Unknown fleet target state: {target_state!r}")
    if path is None:
        path = FLEET_CONTROL_PATH
    atomic_io.atomic_write_json(path, {
        "target_state": target_state,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    })


def _crash_backoff_seconds(streak: int) -> float:
    capped_streak = min(streak, CRASH_BACKOFF_MAX_STREAK)
    return min(CRASH_BACKOFF_MAX_SECONDS, CRASH_BACKOFF_BASE_SECONDS * (2 ** capped_streak))


class SupervisedProcess:
    def __init__(self, name: str, cmd: List[str]):
        self.name = name
        self.cmd = cmd
        self.process: Optional[subprocess.Popen] = None
        self.started_at: Optional[datetime] = None
        self.crash_streak = 0
        self.retry_after: Optional[datetime] = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        logger.info("🚀 Starte %s: %s", self.name, " ".join(self.cmd))
        # CREATE_NEW_PROCESS_GROUP (Windows-only; getattr(...) is a no-op 0 on POSIX, where
        # signal delivery is per-PID already) puts this subprocess in its own process group so
        # a later CTRL_BREAK_EVENT can be targeted at just this process tree instead of also
        # hitting process_supervisor.py's own console — see the GRACEFUL_STOP_TIMEOUT_SECONDS
        # comment above for why this matters.
        #
        # CREATE_NO_WINDOW (found live, 2026-08-19 — a real production outage): when
        # process_supervisor.py itself is launched detached/without its own console (the
        # normal way to run a 24/7 background service), Win32's CreateProcess allocates a
        # BRAND NEW visible console window for this child by default, since neither flag
        # suppressing that was set. That window is a real, closeable thing sitting on the
        # desktop — closing it (or Windows closing it for any reason) sends every process
        # attached to that console a CTRL_CLOSE_EVENT, which took down orchestrator.py,
        # metrics_tracker.py, and everything THEY had spawned (auto_pilot.py, ffmpeg,
        # streamlink — three live, publish=True streamers) all at once, logged only as an
        # opaque "forrtl: error (200): program aborting due to window-CLOSE event" from
        # whichever library happened to be linked against the Fortran runtime. Suppressing
        # window creation entirely removes the thing that could be closed in the first place.
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        self.process = subprocess.Popen(self.cmd, creationflags=creationflags)
        self.started_at = datetime.now(timezone.utc)

    def handle_exit(self) -> None:
        assert self.process is not None
        exit_code = self.process.returncode
        ran_for = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        self.process = None

        if ran_for < CRASH_LOOP_WINDOW_SECONDS:
            self.crash_streak += 1
            backoff = _crash_backoff_seconds(self.crash_streak)
            self.retry_after = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            logger.warning(
                "💥 %s ist nach nur %.0fs abgestürzt (exit=%s) — Absturz #%d in Folge, "
                "warte %.0fs vor dem nächsten Neustart.",
                self.name, ran_for, exit_code, self.crash_streak, backoff,
            )
        else:
            self.crash_streak = 0
            self.retry_after = None
            logger.warning("⚠️ %s wurde unerwartet beendet (exit=%s, lief %.0fs)", self.name, exit_code, ran_for)

    def in_backoff(self) -> bool:
        return self.retry_after is not None and datetime.now(timezone.utc) < self.retry_after

    def _snapshot_descendants(self) -> List["psutil.Process"]:
        """Every process currently rooted at this one (auto_pilot.py per streamer, and
        whatever THEY spawn — ffmpeg, streamlink — at any depth), captured before anything is
        signaled. Must happen first: once a process has exited, psutil can no longer walk its
        children through it, so a snapshot taken only after termination would silently miss
        exactly the orphans this function exists to catch."""
        if self.process is None:
            return []
        try:
            return psutil.Process(self.process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return []

    @staticmethod
    def _kill_if_alive(pid: int) -> None:
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return
            logger.warning("🔪 Erzwinge Beendigung von verbliebenem Prozess PID %d (%s)", pid, proc.name())
            proc.kill()
            proc.wait(timeout=FORCE_KILL_WAIT_SECONDS)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            logger.warning("PID %d reagierte nicht einmal auf kill() — aufgegeben.", pid)

    def stop(self) -> None:
        if self.process is None:
            return
        pid = self.process.pid
        logger.info("⏹️ Beende %s (PID %s) und alle Kindprozesse...", self.name, pid)

        # Snapshot BEFORE signaling anything — see _snapshot_descendants' own docstring.
        descendants = self._snapshot_descendants()

        # Phase 1: a real, catchable interrupt — not terminate()'s unconditional hard kill —
        # gives this process's own KeyboardInterrupt-driven cleanup (already correct in
        # orchestrator.py/metrics_tracker.py) a genuine chance to run.
        try:
            if sys.platform == "win32":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
        except (ProcessLookupError, OSError) as e:
            logger.warning("Konnte kein Stop-Signal an %s senden: %s", self.name, e)

        try:
            self.process.wait(timeout=GRACEFUL_STOP_TIMEOUT_SECONDS)
            logger.info("%s hat sich innerhalb von %ds sauber beendet.", self.name, GRACEFUL_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "%s hat nicht innerhalb von %ds auf das Stop-Signal reagiert.",
                self.name, GRACEFUL_STOP_TIMEOUT_SECONDS,
            )

        # Phase 2 (the actual guarantee): force-kill the root itself (if phase 1 didn't finish
        # in time) and every descendant from the original snapshot — regardless of whether
        # phase 1 succeeded at every intermediate layer. This is what makes "no rogue ffmpeg
        # left behind" true unconditionally, not just when everything upstream behaved.
        self._kill_if_alive(pid)
        for proc in descendants:
            self._kill_if_alive(proc.pid)

        self.process = None


def _raise_keyboard_interrupt(signum, frame) -> None:
    """SIGTERM handler that routes through the exact same try/except KeyboardInterrupt/finally
    shutdown path below as Ctrl+C, instead of duplicating the cleanup logic for a second
    trigger. Matters most for the systemd deployment (deploy/auto-clipping-supervisor.service)
    — `systemctl stop` sends a real SIGTERM, which Python's default handling would otherwise
    just let terminate the process with zero cleanup. On Windows, os.kill(pid, SIGTERM) is
    implemented as an unconditional TerminateProcess that never actually reaches a registered
    handler in the target at all — registering this here is still correct and harmless, just
    inert for that specific delivery path; SupervisedProcess.stop()'s CTRL_BREAK_EVENT is what
    does the real graceful-shutdown work on Windows instead."""
    raise KeyboardInterrupt()


def run_supervisor(
    poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    include_metrics_tracker: bool = True,
    max_iterations: Optional[int] = None,
) -> None:
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    supervised: Dict[str, SupervisedProcess] = {
        "orchestrator": SupervisedProcess("orchestrator.py", [sys.executable, "orchestrator.py"]),
    }
    if include_metrics_tracker:
        supervised["metrics_tracker"] = SupervisedProcess(
            "metrics_tracker.py", [sys.executable, "metrics_tracker.py"]
        )

    logger.info("🛡️ Supervisor gestartet, überwacht: %s", ", ".join(supervised))

    # Respect a pause already requested before this run started (e.g. the supervisor itself
    # was restarted — by process_supervisor's own OS-level service manager, or by hand — while
    # the dashboard's target state was still "paused") instead of starting everything just to
    # stop it again on the very next poll cycle.
    paused = read_fleet_target_state() == FLEET_STATE_PAUSED
    if paused:
        logger.info("⏸️ Fleet startet pausiert (laut %s) — keine Kindprozesse werden gestartet.", FLEET_CONTROL_PATH)
    else:
        for proc in supervised.values():
            proc.start()

    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            time.sleep(poll_interval)

            # Fleet pause/resume, requested via app.py's Start/Stop Fleet button (2026-08-19):
            # this supervisor process itself stays alive and keeps polling either way — only
            # the supervised orchestrator.py/metrics_tracker.py (and, transitively, everything
            # THEY spawn) are stopped/started. Reuses the exact same robust two-phase stop()
            # built for Ctrl+C/SIGTERM shutdown, so a "Stop Fleet" click gets the same
            # no-orphaned-ffmpeg guarantee as killing the supervisor outright.
            target_state = read_fleet_target_state()
            if target_state == FLEET_STATE_PAUSED and not paused:
                logger.info("⏸️ Fleet-Pause über Dashboard angefordert — stoppe alle überwachten Prozesse (Supervisor bleibt aktiv)...")
                for proc in supervised.values():
                    proc.stop()
                paused = True
            elif target_state == FLEET_STATE_RUNNING and paused:
                logger.info("▶️ Fleet-Start über Dashboard angefordert — starte alle überwachten Prozesse neu...")
                for proc in supervised.values():
                    # A pause is a deliberate stop, not a crash — don't carry over crash-loop
                    # backoff state from before it, or a fleet paused during an active backoff
                    # window would silently stay down past the resume click.
                    proc.crash_streak = 0
                    proc.retry_after = None
                    proc.start()
                paused = False

            # While paused, every supervised process is deliberately not running — skip the
            # crash-loop restart check entirely, or it would immediately treat the pause
            # itself as a crash and fight the button click by restarting everything.
            if not paused:
                for proc in supervised.values():
                    if proc.is_running():
                        continue
                    if proc.process is not None:
                        proc.handle_exit()
                    if not proc.in_backoff():
                        proc.start()

            # Ghost-state cleanup (2026-08-19): orchestrator.py's per-streamer auto_pilot.py
            # subprocesses write agent_state_<slug>.json files that nothing ever removes on
            # their own — a streamer taken out of streamers.json, or a subprocess that
            # crashed hard enough to stop updating, leaves a file the dashboard keeps showing
            # forever (found live as a duplicate, stale "papaplatte" entry). Run every poll
            # cycle, not just on startup, so a long-running supervisor stays self-healing
            # instead of only cleaning up right after a restart. Never let a purge failure
            # take the supervisor down — streamers.json being transiently unreadable mid-write
            # is exactly the kind of thing this must degrade past, not crash on.
            try:
                streamers_module.purge_stale_agent_states()
            except Exception as e:
                logger.warning("Ghost-state cleanup failed this cycle: %s", e)
    except KeyboardInterrupt:
        logger.info("Supervisor durch Nutzer gestoppt — beende alle überwachten Prozesse...")
    finally:
        for proc in supervised.values():
            proc.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Top-level supervisor for orchestrator.py and metrics_tracker.py — restarts either one "
        "(with crash-loop backoff) if it dies. Recommended single entry point for 24/7 operation."
    )
    parser.add_argument(
        "--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Seconds between liveness checks (default: %(default)s)",
    )
    parser.add_argument(
        "--skip-metrics-tracker", action="store_true",
        help="Don't supervise metrics_tracker.py (e.g. cookies.json isn't set up yet)",
    )
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after N polling cycles (omit to run forever)")
    args = parser.parse_args()

    run_supervisor(args.poll_interval, include_metrics_tracker=not args.skip_metrics_tracker, max_iterations=args.max_iterations)


if __name__ == "__main__":
    main()
