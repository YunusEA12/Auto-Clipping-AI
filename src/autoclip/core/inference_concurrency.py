"""Bound CPU model memory across the entire streamer fleet using OS-backed locks."""

import os
import time
from contextlib import contextmanager

from filelock import FileLock, Timeout

from autoclip import runtime


def slot_count() -> int:
    available = max(1, min(2, (os.cpu_count() or 2) // 2))
    try:
        requested = int(os.environ.get("WHISPER_MAX_CONCURRENCY", "1"))
        if requested < 1:
            raise ValueError
    except ValueError:
        requested = 1
    return min(available, requested)


@contextmanager
def inference_slot(timeout: float = 1800):
    """Hold through model construction, lazy inference and model release; crash releases lock."""
    folder = runtime.path("temp") / "inference_slots"
    folder.mkdir(parents=True, exist_ok=True)
    locks = [FileLock(folder / f"whisper_{i}.lock") for i in range(slot_count())]
    deadline = time.monotonic() + timeout
    while True:
        for lock in locks:
            try:
                lock.acquire(timeout=0)
            except Timeout:
                continue
            try:
                yield
            finally:
                lock.release()
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Whisper capacity exhausted; retry this chunk later")
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
