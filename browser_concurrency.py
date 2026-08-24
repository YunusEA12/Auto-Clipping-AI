"""Cross-process cap on simultaneous headless-Chrome/Playwright instances (2026-08-24 incident
remediation — see the 2026-08-23 forensic audit).

This pipeline runs one auto_pilot.py process per live streamer, and each one drives its own
TikTok/Instagram Playwright uploads independently — nothing previously bounded how many of
those processes could have a headless Chrome open at once. On 2026-08-23 that unbounded
fan-out (several streamers live simultaneously, each uploading) was a direct contributor to
three separate OOM-kills of chrome-headless/streamlit that evening.

A plain in-process threading.Semaphore can't help here — the concurrency is across OS
processes, not threads within one. Uses the same cross-process FileLock pattern already used
by upload_ledger.py/streamers.py: BROWSER_MAX_CONCURRENCY numbered lock files act as slots;
holding one of them for the lifetime of a browser session is the "permit". Whichever slot a
caller manages to acquire first wins — no ordering/fairness guarantee, but with only 1-2 slots
and short-lived holds (a single upload's worth of browser time) starvation isn't a practical
concern here.
"""

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from filelock import FileLock, Timeout

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

# How many headless-Chrome sessions (TikTok + Instagram Playwright uploads combined) may run
# at once, fleet-wide. Default 2: enough for TikTok and Instagram to upload in parallel for one
# streamer without idling the other platform, while still keeping a hard ceiling regardless of
# how many streamers are simultaneously live. Override via env var if the box's memory budget
# changes.
BROWSER_MAX_CONCURRENCY = int(os.environ.get("BROWSER_MAX_CONCURRENCY", "2"))

_SLOT_DIR = Path("browser_slots")
_SLOT_POLL_SECONDS = 1.0

# 2026-08-24 dashboard-stall hardening: every current call site now passes this explicit cap
# (see each one's own try/except TimeoutError) instead of waiting forever — with
# BROWSER_MAX_CONCURRENCY=2 shared fleet-wide, a single genuinely-stuck Playwright session
# (a hung driver/launch, not reproduced today but structurally possible — see this module's
# own top-of-file incident note) would otherwise starve every OTHER streamer's uploads
# indefinitely, one poll-loop iteration at a time, with nothing in the log distinguishing
# "briefly busy" from "permanently wedged."
DEFAULT_SLOT_TIMEOUT_SECONDS = 120


@contextmanager
def browser_slot(timeout: Optional[float] = DEFAULT_SLOT_TIMEOUT_SECONDS):
    """Blocks until one of BROWSER_MAX_CONCURRENCY slots is free, then holds it until the
    `with` block exits. Wrap the entire Playwright session (launch through close), not just
    the launch() call itself — the memory pressure comes from the browser running, not from
    starting it.

    Raises TimeoutError if no slot frees up within `timeout` seconds (pass `timeout=None` to
    wait indefinitely instead — not recommended for an unattended pipeline; see
    DEFAULT_SLOT_TIMEOUT_SECONDS above for why). Every current call site catches this and
    treats it exactly like any other platform failure (log, return a failed outcome, retried
    next cycle) rather than letting it propagate — see e.g. tiktok_uploader.upload_video()'s
    own try/except around this call."""
    _SLOT_DIR.mkdir(exist_ok=True)
    locks = [FileLock(str(_SLOT_DIR / f"slot_{i}.lock")) for i in range(BROWSER_MAX_CONCURRENCY)]
    deadline = None if timeout is None else time.monotonic() + timeout
    waited_any = False

    while True:
        for lock in locks:
            try:
                lock.acquire(timeout=0.05)
            except Timeout:
                continue
            if waited_any:
                logger.info("Acquired browser slot after waiting")
            try:
                yield
            finally:
                lock.release()
            return

        waited_any = True
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for a free browser slot "
                f"(BROWSER_MAX_CONCURRENCY={BROWSER_MAX_CONCURRENCY} all in use)"
            )
        logger.info("All %d browser slot(s) in use — waiting for one to free up", BROWSER_MAX_CONCURRENCY)
        time.sleep(_SLOT_POLL_SECONDS)
