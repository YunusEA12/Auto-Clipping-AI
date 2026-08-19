"""process_supervisor.py used to be named watchdog.py, which silently shadowed the
third-party `watchdog` package (a Streamlit file-watcher dependency) for every script run
from this project's root — since the current directory takes precedence on sys.path,
`import watchdog` anywhere in the project resolved to our file instead of the real package,
breaking `streamlit run app.py` outright. Caught by actually launching Streamlit, not by
reasoning about it — these tests are the regression guard against reintroducing a module
name that collides with a real installed package."""

import subprocess
import sys

import psutil
import pytest

import process_supervisor


def test_watchdog_import_resolves_to_the_real_package_not_ours():
    import watchdog
    assert "site-packages" in watchdog.__file__.replace("\\", "/")
    assert watchdog.__file__ != process_supervisor.__file__


def test_no_module_named_watchdog_in_this_project():
    from pathlib import Path
    project_root = Path(process_supervisor.__file__).parent
    assert not (project_root / "watchdog.py").exists()


def test_crash_backoff_still_works_under_the_new_name():
    delays = [process_supervisor._crash_backoff_seconds(streak) for streak in (1, 2, 3)]
    assert delays == sorted(delays)


# --- Ghost-state cleanup wired into the poll loop (2026-08-19: found live — a leftover flat
# agent_state.json sat alongside the real per-streamer file, both showing "papaplatte" in the
# dashboard, one of them 9+ hours stale — process_supervisor.py now purges these every poll
# cycle, not just on startup, so a long-running supervisor stays self-healing) -------------

def test_purge_runs_every_poll_cycle(monkeypatch):
    # Real subprocess spawning is stubbed out — this test only cares that the ghost-state
    # purge gets called from the loop, not that orchestrator.py/metrics_tracker.py actually run.
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "start", lambda self: None)
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "is_running", lambda self: True)
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "stop", lambda self: None)

    calls = []
    monkeypatch.setattr(process_supervisor.streamers_module, "purge_stale_agent_states", lambda: calls.append(1))

    process_supervisor.run_supervisor(poll_interval=0, max_iterations=3, include_metrics_tracker=False)

    assert len(calls) == 3


def test_purge_failure_does_not_crash_the_supervisor(monkeypatch):
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "start", lambda self: None)
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "is_running", lambda self: True)
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "stop", lambda self: None)

    def boom():
        raise RuntimeError("streamers.json transiently unreadable")
    monkeypatch.setattr(process_supervisor.streamers_module, "purge_stale_agent_states", boom)

    # Must not raise.
    process_supervisor.run_supervisor(poll_interval=0, max_iterations=2, include_metrics_tracker=False)


# --- Robust shutdown (2026-08-19: found live — Ctrl+C on the supervisor left ffmpeg/
# streamlink recording processes running in the background indefinitely, because
# SupervisedProcess.stop() used to send a plain terminate() — an unconditional hard kill on
# Windows, with zero chance for orchestrator.py's own already-correct KeyboardInterrupt
# cleanup to run at all, let alone its own children's children) --------------------------

class FakePsutilProcess:
    """Same fake used by test_orchestrator_reconciliation.py, extended with kill()/name()
    for the force-kill safety net."""

    def __init__(self, pid, running=True, status="running", name="fake.exe"):
        self.pid = pid
        self._running = running
        self._status = status
        self._name = name
        self.killed = False

    def is_running(self):
        return self._running

    def status(self):
        return self._status

    def name(self):
        return self._name

    def kill(self):
        self.killed = True
        self._running = False

    def wait(self, timeout=None):
        pass

    def children(self, recursive=False):
        return []


class FakePopen:
    def __init__(self, pid):
        self.pid = pid
        self.signals_sent = []
        self.wait_calls = 0
        self.wait_should_time_out = False

    def send_signal(self, sig):
        self.signals_sent.append(sig)

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_should_time_out:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

    def poll(self):
        return None


def _make_supervised(monkeypatch, root_pid=100, descendants=None, wait_should_time_out=False, platform="win32"):
    proc = process_supervisor.SupervisedProcess("fake.py", ["fake.py"])
    fake_popen = FakePopen(root_pid)
    fake_popen.wait_should_time_out = wait_should_time_out
    proc.process = fake_popen

    # The root's psutil-level liveness must mirror whether its Popen-level wait() actually
    # succeeded — a real OS process that Popen.wait() confirmed exited is genuinely gone by
    # the time _kill_if_alive() looks it up again; only a real timeout leaves it truly alive.
    registry = {root_pid: FakePsutilProcess(root_pid, running=wait_should_time_out, name="fake_root")}
    for d in (descendants or []):
        registry[d.pid] = d

    def fake_process_ctor(pid):
        if pid not in registry:
            raise psutil.NoSuchProcess(pid)
        return registry[pid]

    monkeypatch.setattr(process_supervisor.psutil, "Process", fake_process_ctor)
    monkeypatch.setattr(process_supervisor.sys, "platform", platform)
    # children(recursive=True) is looked up through the ROOT's own psutil.Process object.
    registry[root_pid].children = lambda recursive=False: (descendants or [])

    return proc, fake_popen, registry


def test_stop_sends_ctrl_break_on_windows(monkeypatch):
    proc, fake_popen, _ = _make_supervised(monkeypatch, platform="win32")
    proc.stop()
    assert fake_popen.signals_sent == [process_supervisor.signal.CTRL_BREAK_EVENT]


def test_stop_sends_sigint_on_posix(monkeypatch):
    proc, fake_popen, _ = _make_supervised(monkeypatch, platform="linux")
    proc.stop()
    assert fake_popen.signals_sent == [process_supervisor.signal.SIGINT]


def test_stop_does_not_force_kill_when_graceful_shutdown_succeeds(monkeypatch):
    descendant = FakePsutilProcess(pid=101, running=False)  # already exited by the time we check
    proc, fake_popen, registry = _make_supervised(monkeypatch, descendants=[descendant], wait_should_time_out=False)

    proc.stop()

    assert registry[100].killed is False
    assert descendant.killed is False
    assert proc.process is None


def test_stop_force_kills_root_and_descendants_after_timeout(monkeypatch):
    descendant = FakePsutilProcess(pid=101, running=True)
    proc, fake_popen, registry = _make_supervised(monkeypatch, descendants=[descendant], wait_should_time_out=True)

    proc.stop()

    assert registry[100].killed is True  # root was still alive after the graceful-wait timeout
    assert descendant.killed is True  # this is the actual orphaned-ffmpeg guarantee
    assert proc.process is None


def test_stop_snapshots_descendants_before_sending_the_stop_signal(monkeypatch):
    # If the snapshot were taken AFTER signaling, a descendant that already exited as a side
    # effect of the signal would never be seen at all — silently defeating the safety net for
    # exactly the processes it exists to catch.
    order = []
    descendant = FakePsutilProcess(pid=101, running=True)
    proc, fake_popen, registry = _make_supervised(monkeypatch, descendants=[descendant])

    real_children = registry[100].children
    registry[100].children = lambda recursive=False: (order.append("snapshot") or real_children(recursive))
    original_send_signal = fake_popen.send_signal
    fake_popen.send_signal = lambda sig: (order.append("signal") or original_send_signal(sig))

    proc.stop()

    assert order == ["snapshot", "signal"]


def test_stop_is_a_no_op_when_nothing_is_running():
    proc = process_supervisor.SupervisedProcess("fake.py", ["fake.py"])
    proc.process = None
    proc.stop()  # must not raise


def test_kill_if_alive_skips_a_pid_that_is_already_gone(monkeypatch):
    def raise_no_such(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(process_supervisor.psutil, "Process", raise_no_such)

    process_supervisor.SupervisedProcess._kill_if_alive(999)  # must not raise


def test_start_uses_new_process_group_on_windows(monkeypatch):
    calls = []

    class FakePopenCapture:
        def __init__(self, cmd, creationflags=None):
            calls.append(creationflags)
            self.pid = 1

    monkeypatch.setattr(process_supervisor.subprocess, "Popen", FakePopenCapture)
    monkeypatch.setattr(process_supervisor.sys, "platform", "win32")

    proc = process_supervisor.SupervisedProcess("fake.py", ["fake.py"])
    proc.start()

    assert calls == [subprocess.CREATE_NEW_PROCESS_GROUP]


# --- SIGTERM handling (2026-08-19: required for the systemd deployment — `systemctl stop`
# sends SIGTERM, not SIGINT, and Python's default SIGTERM handling has zero cleanup) --------

def test_sigterm_handler_raises_keyboard_interrupt():
    with pytest.raises(KeyboardInterrupt):
        process_supervisor._raise_keyboard_interrupt(process_supervisor.signal.SIGTERM, None)


def test_run_supervisor_registers_a_sigterm_handler(monkeypatch):
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "start", lambda self: None)
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "is_running", lambda self: True)
    monkeypatch.setattr(process_supervisor.SupervisedProcess, "stop", lambda self: None)
    monkeypatch.setattr(process_supervisor.streamers_module, "purge_stale_agent_states", lambda: None)

    registered = []
    monkeypatch.setattr(
        process_supervisor.signal, "signal",
        lambda signum, handler: registered.append((signum, handler)),
    )

    process_supervisor.run_supervisor(poll_interval=0, max_iterations=1, include_metrics_tracker=False)

    assert registered == [(process_supervisor.signal.SIGTERM, process_supervisor._raise_keyboard_interrupt)]


# --- Fleet Start/Stop via app.py's dashboard button (2026-08-19: terminal-based start/stop
# was frustrating for iterative testing — fleet_control.json lets app.py request a pause/
# resume without process_supervisor.py itself ever exiting) --------------------------------

def test_read_fleet_target_state_defaults_to_running_when_file_missing(tmp_path):
    assert process_supervisor.read_fleet_target_state(tmp_path / "missing.json") == process_supervisor.FLEET_STATE_RUNNING


def test_read_fleet_target_state_defaults_to_running_on_corrupt_file(tmp_path):
    path = tmp_path / "fleet_control.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert process_supervisor.read_fleet_target_state(path) == process_supervisor.FLEET_STATE_RUNNING


def test_read_fleet_target_state_defaults_to_running_on_unrecognized_value(tmp_path):
    path = tmp_path / "fleet_control.json"
    path.write_text('{"target_state": "sleeping"}', encoding="utf-8")
    assert process_supervisor.read_fleet_target_state(path) == process_supervisor.FLEET_STATE_RUNNING


def test_write_then_read_fleet_target_state_roundtrips(tmp_path):
    path = tmp_path / "fleet_control.json"
    process_supervisor.write_fleet_target_state(process_supervisor.FLEET_STATE_PAUSED, path)
    assert process_supervisor.read_fleet_target_state(path) == process_supervisor.FLEET_STATE_PAUSED

    process_supervisor.write_fleet_target_state(process_supervisor.FLEET_STATE_RUNNING, path)
    assert process_supervisor.read_fleet_target_state(path) == process_supervisor.FLEET_STATE_RUNNING


def test_write_fleet_target_state_rejects_unknown_value(tmp_path):
    with pytest.raises(ValueError):
        process_supervisor.write_fleet_target_state("sleeping", tmp_path / "fleet_control.json")


class _CountingSupervisedProcess:
    """Stands in for SupervisedProcess in run_supervisor()'s loop without touching real OS
    processes — tracks start()/stop() call counts and reports is_running() based on whether
    it's currently "started", so the pause-guard's crash-loop-restart skip can be verified."""

    def __init__(self, name, cmd):
        self.name = name
        self.cmd = cmd
        self.started = False
        self.start_calls = 0
        self.stop_calls = 0
        self.crash_streak = 0
        self.retry_after = None
        self.process = None

    def start(self):
        self.started = True
        self.process = object()
        self.start_calls += 1

    def stop(self):
        # Idempotent, same as the real SupervisedProcess.stop() (a no-op once self.process is
        # already None) — run_supervisor()'s own `finally` block unconditionally calls stop()
        # on every supervised process regardless of whether it was already stopped, so a fake
        # that isn't idempotent would over-count calls made after an already-paused shutdown.
        if not self.started:
            return
        self.started = False
        self.process = None
        self.stop_calls += 1

    def is_running(self):
        return self.started

    def handle_exit(self):
        pass

    def in_backoff(self):
        return False


def test_run_supervisor_stops_everything_when_paused_via_dashboard(tmp_path, monkeypatch):
    control_path = tmp_path / "fleet_control.json"
    monkeypatch.setattr(process_supervisor, "FLEET_CONTROL_PATH", control_path)

    instances = []

    def factory(name, cmd):
        p = _CountingSupervisedProcess(name, cmd)
        instances.append(p)
        return p

    monkeypatch.setattr(process_supervisor, "SupervisedProcess", factory)
    monkeypatch.setattr(process_supervisor.streamers_module, "purge_stale_agent_states", lambda: None)

    # Pause is requested only once the loop is already running (not before start), so the
    # test can assert the sequence: start -> (pause requested) -> stop, without racing sleep(0).
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            process_supervisor.write_fleet_target_state(process_supervisor.FLEET_STATE_PAUSED, control_path)

    monkeypatch.setattr(process_supervisor.time, "sleep", fake_sleep)

    process_supervisor.run_supervisor(poll_interval=0, max_iterations=3, include_metrics_tracker=False)

    orchestrator = instances[0]
    # Started once at launch, then explicitly stopped once the pause was observed, and never
    # restarted afterward despite is_running() now reporting False every remaining cycle (the
    # crash-loop guard skipping restart entirely while paused is exactly what this asserts).
    assert orchestrator.start_calls == 1
    assert orchestrator.stop_calls == 1
    assert orchestrator.started is False


def test_run_supervisor_resumes_after_pause_is_lifted(tmp_path, monkeypatch):
    control_path = tmp_path / "fleet_control.json"
    monkeypatch.setattr(process_supervisor, "FLEET_CONTROL_PATH", control_path)
    process_supervisor.write_fleet_target_state(process_supervisor.FLEET_STATE_PAUSED, control_path)

    instances = []

    def factory(name, cmd):
        p = _CountingSupervisedProcess(name, cmd)
        instances.append(p)
        return p

    monkeypatch.setattr(process_supervisor, "SupervisedProcess", factory)
    monkeypatch.setattr(process_supervisor.streamers_module, "purge_stale_agent_states", lambda: None)

    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 1:
            process_supervisor.write_fleet_target_state(process_supervisor.FLEET_STATE_RUNNING, control_path)

    monkeypatch.setattr(process_supervisor.time, "sleep", fake_sleep)

    process_supervisor.run_supervisor(poll_interval=0, max_iterations=2, include_metrics_tracker=False)

    orchestrator = instances[0]
    # Started paused (0 calls at launch, per the "respect an already-paused target state on
    # startup" behavior), then started exactly once after the resume was observed. stop_calls
    # is 1 too: run_supervisor()'s own final `finally` block always tears everything down when
    # the function itself returns, independent of the pause feature.
    assert orchestrator.start_calls == 1
    assert orchestrator.stop_calls == 1


def test_run_supervisor_starts_paused_when_already_requested_before_launch(tmp_path, monkeypatch):
    control_path = tmp_path / "fleet_control.json"
    monkeypatch.setattr(process_supervisor, "FLEET_CONTROL_PATH", control_path)
    process_supervisor.write_fleet_target_state(process_supervisor.FLEET_STATE_PAUSED, control_path)

    instances = []

    def factory(name, cmd):
        p = _CountingSupervisedProcess(name, cmd)
        instances.append(p)
        return p

    monkeypatch.setattr(process_supervisor, "SupervisedProcess", factory)
    monkeypatch.setattr(process_supervisor.streamers_module, "purge_stale_agent_states", lambda: None)
    monkeypatch.setattr(process_supervisor.time, "sleep", lambda seconds: None)

    process_supervisor.run_supervisor(poll_interval=0, max_iterations=1, include_metrics_tracker=False)

    orchestrator = instances[0]
    assert orchestrator.start_calls == 0  # never started at all — target was already "paused"


def test_streamlit_actually_imports_its_own_file_watcher_dependency():
    from pathlib import Path

    # The concrete failure mode only reproduces when run from the project root (that's what
    # put our file on sys.path ahead of site-packages) — cwd is set explicitly so this test
    # actually exercises that condition, not just wherever pytest happens to be invoked from.
    project_root = Path(process_supervisor.__file__).parent
    result = subprocess.run(
        [sys.executable, "-c", "from streamlit.watcher.event_based_path_watcher import EventBasedPathWatcher"],
        capture_output=True, text=True, timeout=30, cwd=project_root,
    )
    assert result.returncode == 0, result.stderr
