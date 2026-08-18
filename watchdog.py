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

This is still a single point of failure at the very top: if THIS process's host machine goes
down, or this process itself is killed with nothing outside it to restart it, the fleet stops.
Turning it into a true no-single-point-of-failure setup means wiring it into an OS-level
service manager (Windows Task Scheduler "at startup" trigger, or NSSM as a Windows service)
so the OS restarts it automatically after a reboot — that's a one-time, machine-specific setup
step outside what a Python script can safely do on its own (it needs admin rights / GUI
interaction), so it's left as an explicit manual step rather than attempted here.

Usage:
    python watchdog.py
    python watchdog.py --poll-interval 30
    python watchdog.py --skip-metrics-tracker   # e.g. cookies.json isn't set up yet
"""

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 30
CRASH_LOOP_WINDOW_SECONDS = 60
CRASH_BACKOFF_BASE_SECONDS = 30
CRASH_BACKOFF_MAX_SECONDS = 1800
CRASH_BACKOFF_MAX_STREAK = 20


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
        self.process = subprocess.Popen(self.cmd)
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

    def stop(self) -> None:
        if self.process is None:
            return
        logger.info("⏹️ Beende %s (PID %s)", self.name, self.process.pid)
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=15)


def run_watchdog(
    poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS,
    include_metrics_tracker: bool = True,
    max_iterations: Optional[int] = None,
) -> None:
    supervised: Dict[str, SupervisedProcess] = {
        "orchestrator": SupervisedProcess("orchestrator.py", [sys.executable, "orchestrator.py"]),
    }
    if include_metrics_tracker:
        supervised["metrics_tracker"] = SupervisedProcess(
            "metrics_tracker.py", [sys.executable, "metrics_tracker.py"]
        )

    logger.info("🛡️ Watchdog gestartet, überwacht: %s", ", ".join(supervised))

    for proc in supervised.values():
        proc.start()

    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            time.sleep(poll_interval)

            for proc in supervised.values():
                if proc.is_running():
                    continue
                if proc.process is not None:
                    proc.handle_exit()
                if not proc.in_backoff():
                    proc.start()
    except KeyboardInterrupt:
        logger.info("Watchdog durch Nutzer gestoppt — beende alle überwachten Prozesse...")
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

    run_watchdog(args.poll_interval, include_metrics_tracker=not args.skip_metrics_tracker, max_iterations=args.max_iterations)


if __name__ == "__main__":
    main()
