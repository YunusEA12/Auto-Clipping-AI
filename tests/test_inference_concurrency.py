import os
import subprocess
import sys

import pytest

from autoclip.core.inference_concurrency import inference_slot, slot_count


def test_slot_is_exclusive_across_processes_and_released_after_crash(monkeypatch):
    monkeypatch.setenv("WHISPER_MAX_CONCURRENCY", "1")
    code = "from autoclip.core.inference_concurrency import inference_slot; import time; slot=inference_slot(); slot.__enter__(); print('held',flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(TimeoutError), inference_slot(timeout=0):
            pytest.fail("Occupied slot acquired")
    finally:
        child.terminate()
        child.wait(timeout=10)
        child.stdout.close()
    with inference_slot(timeout=1):
        pass


def test_slot_configuration_is_bounded(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    for value in ("wrong", "0", "-1"):
        monkeypatch.setenv("WHISPER_MAX_CONCURRENCY", value)
        assert slot_count() == 1
    monkeypatch.setenv("WHISPER_MAX_CONCURRENCY", "100")
    assert slot_count() == 2
