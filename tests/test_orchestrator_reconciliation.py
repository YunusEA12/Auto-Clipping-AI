import json
import subprocess
from datetime import datetime, timedelta, timezone

import psutil
import pytest

import orchestrator


# --- Startup reconciliation (2026-08-18: H-14 — orchestrator.py used to start every run
# with tracked={}, so a restart while a streamer was still being recorded orphaned that
# subprocess and started a brand-new, duplicate one for the same streamer) -----------------

class FakePsutilProcess:
    def __init__(self, pid, cmdline=None, running=True, status="running", cwd=None):
        self.pid = pid
        self._cmdline = cmdline or []
        self._running = running
        self._status = status
        # Defaults to orchestrator's own directory so every existing test (written before the
        # cwd check existed) keeps matching by default -- only tests explicitly about the cwd
        # check itself pass a mismatched one.
        self._cwd = cwd if cwd is not None else str(orchestrator.THIS_DIR)
        self.terminated = False
        self.killed = False

    def cmdline(self):
        return self._cmdline

    def cwd(self):
        return self._cwd

    def is_running(self):
        return self._running

    def status(self):
        return self._status

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self._running:
            raise psutil.TimeoutExpired(timeout, pid=self.pid)


# --- _is_still_our_auto_pilot ---------------------------------------------------------

def test_is_still_our_auto_pilot_true_when_cmdline_matches(monkeypatch):
    fake = FakePsutilProcess(123, cmdline=["python", "auto_pilot.py", "--streamer-name", "eliasn97"])
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    assert orchestrator._is_still_our_auto_pilot(123, "eliasn97") is True


def test_is_still_our_auto_pilot_false_when_different_streamer(monkeypatch):
    fake = FakePsutilProcess(123, cmdline=["python", "auto_pilot.py", "--streamer-name", "papaplatte"])
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    assert orchestrator._is_still_our_auto_pilot(123, "eliasn97") is False


def test_is_still_our_auto_pilot_false_when_pid_gone(monkeypatch):
    def raise_no_such(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(orchestrator.psutil, "Process", raise_no_such)
    assert orchestrator._is_still_our_auto_pilot(123, "eliasn97") is False


# --- cwd check (2026-08-21: found live -- a stale orchestrator_state.json carried a PID that
# was still alive and matched by cmdline, but belonged to an entirely different, un-updated
# checkout of this script running as root in a leftover screen session. Real recording
# happened there for hours; every clip silently failed at analysis because that checkout's
# .env was missing GEMINI_API_KEY, and the mismatch was never surfaced.) ---------------------

def test_is_still_our_auto_pilot_false_when_cwd_does_not_match(monkeypatch):
    fake = FakePsutilProcess(
        123, cmdline=["python", "auto_pilot.py", "--streamer-name", "eliasn97"],
        cwd="/root/Auto-Clipping-AI",
    )
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    assert orchestrator._is_still_our_auto_pilot(123, "eliasn97") is False


def test_is_still_our_auto_pilot_true_when_cwd_matches_explicitly(monkeypatch):
    fake = FakePsutilProcess(
        123, cmdline=["python", "auto_pilot.py", "--streamer-name", "eliasn97"],
        cwd=str(orchestrator.THIS_DIR),
    )
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    assert orchestrator._is_still_our_auto_pilot(123, "eliasn97") is True


# --- reconcile_with_running_subprocesses ------------------------------------------------

def test_reconcile_adopts_a_still_running_streamer(tmp_path, monkeypatch):
    state_path = tmp_path / "orchestrator_state.json"
    started_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    state_path.write_text(json.dumps({
        "streamers": {"eliasn97": {"recording": True, "pid": 123, "started_at": started_at}},
    }), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_is_still_our_auto_pilot", lambda pid, name: True)
    monkeypatch.setattr(orchestrator, "_AdoptedProcess", lambda pid: f"adopted-{pid}")

    tracked = orchestrator.reconcile_with_running_subprocesses(state_path)

    assert "eliasn97" in tracked
    assert tracked["eliasn97"]["process"] == "adopted-123"
    assert tracked["eliasn97"]["started_at"].isoformat() == started_at


def test_reconcile_skips_a_streamer_whose_pid_is_gone(tmp_path, monkeypatch):
    state_path = tmp_path / "orchestrator_state.json"
    state_path.write_text(json.dumps({
        "streamers": {"eliasn97": {"recording": True, "pid": 123, "started_at": None}},
    }), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_is_still_our_auto_pilot", lambda pid, name: False)

    assert orchestrator.reconcile_with_running_subprocesses(state_path) == {}


def test_reconcile_skips_streamer_not_marked_recording(tmp_path, monkeypatch):
    state_path = tmp_path / "orchestrator_state.json"
    state_path.write_text(json.dumps({
        "streamers": {"eliasn97": {"recording": False, "pid": 123}},
    }), encoding="utf-8")
    monkeypatch.setattr(orchestrator, "_is_still_our_auto_pilot", lambda pid, name: True)

    assert orchestrator.reconcile_with_running_subprocesses(state_path) == {}


def test_reconcile_returns_empty_when_no_state_file(tmp_path):
    assert orchestrator.reconcile_with_running_subprocesses(tmp_path / "missing.json") == {}


# --- _AdoptedProcess ---------------------------------------------------------------------
# Wraps psutil.Process behind the same .pid/.poll()/.returncode/.terminate()/.kill()/.wait()
# interface stop_recording() and the crash-loop poll already use for a real subprocess.Popen
# — so an adopted (not self-spawned) subprocess can be tracked and stopped identically.

def test_adopted_process_poll_returns_none_while_running(monkeypatch):
    fake = FakePsutilProcess(123, running=True)
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    adopted = orchestrator._AdoptedProcess(123)
    assert adopted.poll() is None


def test_adopted_process_poll_returns_returncode_once_exited(monkeypatch):
    fake = FakePsutilProcess(123, running=False)
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    adopted = orchestrator._AdoptedProcess(123)
    assert adopted.poll() == 0
    assert adopted.returncode == 0


def test_adopted_process_wait_raises_subprocess_timeout_when_still_running(monkeypatch):
    fake = FakePsutilProcess(123, running=True)
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    adopted = orchestrator._AdoptedProcess(123)
    with pytest.raises(subprocess.TimeoutExpired):
        adopted.wait(timeout=1)


def test_adopted_process_terminate_and_kill_delegate_to_psutil(monkeypatch):
    fake = FakePsutilProcess(123)
    monkeypatch.setattr(orchestrator.psutil, "Process", lambda pid: fake)
    adopted = orchestrator._AdoptedProcess(123)
    adopted.terminate()
    adopted.kill()
    assert fake.terminated is True
    assert fake.killed is True
