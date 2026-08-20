"""dashboard_api.py is the interface boundary between app.py and the pipeline modules
(M-06 in the audit) — these tests confirm every function app.py actually calls exists with
the expected signature and correctly delegates to the underlying module, using the same
monkeypatch-the-real-module isolation pattern as the rest of this suite (never touching real
project state files)."""

import subprocess

import pytest

import dashboard_api
import streamers as streamers_module
import profiles


def test_paths_are_reexported_from_the_real_modules():
    import analyze, auto_pilot, orchestrator, train_loop
    assert dashboard_api.AGENT_STATE_PATH == auto_pilot.AGENT_STATE_PATH
    assert dashboard_api.AI_GUIDELINES_PATH == analyze.AI_GUIDELINES_PATH
    assert dashboard_api.ORCHESTRATOR_STATE_PATH == orchestrator.ORCHESTRATOR_STATE_PATH
    assert dashboard_api.VIRAL_MEMORY_PATH == train_loop.VIRAL_MEMORY_PATH


def test_layout_constants_match_process_module():
    import process
    assert dashboard_api.LAYOUT_AUTO == process.LAYOUT_AUTO
    assert dashboard_api.LAYOUT_SPLIT_SCREEN == process.LAYOUT_SPLIT_SCREEN
    assert dashboard_api.LAYOUT_BLUR_BACKGROUND == process.LAYOUT_BLUR_BACKGROUND
    assert dashboard_api.LAYOUT_FULL_CAM == process.LAYOUT_FULL_CAM
    assert dashboard_api.HIGHLIGHT_COLORS == process.HIGHLIGHT_COLORS


# --- list_agent_state_paths (2026-08-18: H-14 — two streamers live at once used to both
# write the single shared agent_state.json, corrupting each other's dashboard state; each
# now writes its own agent_state_<slug>.json and app.py reads every one of them) -----------

def test_list_agent_state_paths_finds_legacy_and_per_streamer_files(tmp_path):
    (tmp_path / "agent_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agent_state_eliasn97.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agent_state_papaplatte.json").write_text("{}", encoding="utf-8")
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")

    paths = dashboard_api.list_agent_state_paths(root=tmp_path)

    assert {p.name for p in paths} == {
        "agent_state.json", "agent_state_eliasn97.json", "agent_state_papaplatte.json",
    }


def test_list_agent_state_paths_empty_when_none_exist(tmp_path):
    assert dashboard_api.list_agent_state_paths(root=tmp_path) == []


def test_purge_stale_agent_states_delegates_to_streamers_module(monkeypatch):
    calls = []
    monkeypatch.setattr(streamers_module, "purge_stale_agent_states", lambda: calls.append(1) or ["x.json"])
    result = dashboard_api.purge_stale_agent_states()
    assert calls == [1]
    assert result == ["x.json"]


# --- agent_live_status_by_slug (2026-08-20: sidebar Agent Status wrongly showed "Offline"
# for a streamer that had just restarted and was genuinely live/recording, purely because its
# own agent_state file hadn't caught up yet -- this cross-references orchestrator_state.json's
# real-time ground truth, the same source the main "Konfigurierte Streamer" section trusts) ---

def test_agent_live_status_by_slug_reflects_orchestrator_state(tmp_path, monkeypatch):
    path = tmp_path / "streamers.json"
    monkeypatch.setattr(streamers_module, "STREAMERS_PATH", path)
    dashboard_api.add_streamer("coachlim", "https://twitch.tv/coachlim", auto_upload=True)
    dashboard_api.add_streamer("marli", "https://twitch.tv/marli", auto_upload=True)

    orchestrator_state = {
        "streamers": {
            "coachlim": {"live": True, "recording": True},
            "marli": {"live": False, "recording": False},
        }
    }

    result = dashboard_api.agent_live_status_by_slug(orchestrator_state)

    assert result["coachlim"] == {"live": True, "recording": True}
    assert result["marli"] == {"live": False, "recording": False}


def test_agent_live_status_by_slug_defaults_to_false_when_streamer_missing_from_state(tmp_path, monkeypatch):
    path = tmp_path / "streamers.json"
    monkeypatch.setattr(streamers_module, "STREAMERS_PATH", path)
    dashboard_api.add_streamer("coachlim", "https://twitch.tv/coachlim", auto_upload=True)

    # orchestrator.py itself isn't running / hasn't reported on this streamer yet.
    result = dashboard_api.agent_live_status_by_slug({})

    assert result["coachlim"] == {"live": False, "recording": False}


def test_agent_live_status_by_slug_handles_none_orchestrator_state(tmp_path, monkeypatch):
    path = tmp_path / "streamers.json"
    monkeypatch.setattr(streamers_module, "STREAMERS_PATH", path)
    dashboard_api.add_streamer("coachlim", "https://twitch.tv/coachlim", auto_upload=True)

    result = dashboard_api.agent_live_status_by_slug(None)

    assert result["coachlim"] == {"live": False, "recording": False}


def test_streamer_crud_roundtrip_through_the_facade(tmp_path, monkeypatch):
    path = tmp_path / "streamers.json"
    monkeypatch.setattr(streamers_module, "STREAMERS_PATH", path)

    dashboard_api.add_streamer("elias", "https://twitch.tv/elias", profile="p1", auto_upload=True)
    entries = dashboard_api.load_streamers()
    assert [e["name"] for e in entries] == ["elias"]
    assert entries[0]["auto_upload"] is True

    assert dashboard_api.remove_streamer("elias") is True
    assert dashboard_api.load_streamers() == []


# --- Fleet Start/Stop (2026-08-19) — app.py's Start/Stop Fleet button, thinly delegating to
# process_supervisor.py's own fleet_control.json read/write ---------------------------------

def test_fleet_state_constants_match_process_supervisor():
    import process_supervisor
    assert dashboard_api.FLEET_STATE_RUNNING == process_supervisor.FLEET_STATE_RUNNING
    assert dashboard_api.FLEET_STATE_PAUSED == process_supervisor.FLEET_STATE_PAUSED


def test_set_then_read_fleet_target_state_roundtrips(tmp_path, monkeypatch):
    import process_supervisor
    monkeypatch.setattr(process_supervisor, "FLEET_CONTROL_PATH", tmp_path / "fleet_control.json")

    dashboard_api.set_fleet_target_state(dashboard_api.FLEET_STATE_PAUSED)
    assert dashboard_api.read_fleet_target_state() == dashboard_api.FLEET_STATE_PAUSED

    dashboard_api.set_fleet_target_state(dashboard_api.FLEET_STATE_RUNNING)
    assert dashboard_api.read_fleet_target_state() == dashboard_api.FLEET_STATE_RUNNING


def test_read_fleet_target_state_defaults_to_running_without_touching_real_project_state(tmp_path, monkeypatch):
    import process_supervisor
    monkeypatch.setattr(process_supervisor, "FLEET_CONTROL_PATH", tmp_path / "never_written.json")
    assert dashboard_api.read_fleet_target_state() == dashboard_api.FLEET_STATE_RUNNING


# --- Remote deployment control (2026-08-21: "Git Pull & Restart" button, VPS-only) ----------

def test_is_systemd_deployment_true_when_unit_file_present(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_api, "_SYSTEMD_UNIT_DIR", tmp_path)
    (tmp_path / f"{dashboard_api.SUPERVISOR_SYSTEMD_UNIT}.service").write_text("", encoding="utf-8")
    assert dashboard_api.is_systemd_deployment() is True


def test_is_systemd_deployment_false_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_api, "_SYSTEMD_UNIT_DIR", tmp_path)
    assert dashboard_api.is_systemd_deployment() is False


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_git_pull_and_restart_supervisor_success(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "pull"]:
            return _FakeCompletedProcess(returncode=0, stdout="Already up to date.\n")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    success, detail = dashboard_api.git_pull_and_restart_supervisor()

    assert success is True
    assert "Already up to date" in detail
    assert calls[0] == ["git", "pull"]
    assert calls[1] == ["sudo", "systemctl", "restart", dashboard_api.SUPERVISOR_SYSTEMD_UNIT]


def test_git_pull_and_restart_supervisor_stops_on_git_failure_never_restarts(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess(returncode=1, stderr="merge conflict")

    monkeypatch.setattr(subprocess, "run", fake_run)

    success, detail = dashboard_api.git_pull_and_restart_supervisor()

    assert success is False
    assert "merge conflict" in detail
    assert len(calls) == 1  # never even attempted the restart


def test_git_pull_and_restart_supervisor_reports_restart_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "pull"]:
            return _FakeCompletedProcess(returncode=0, stdout="ok")
        return _FakeCompletedProcess(returncode=1, stderr="Unit not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    success, detail = dashboard_api.git_pull_and_restart_supervisor()

    assert success is False
    assert "Unit not found" in detail


def test_git_pull_and_restart_supervisor_handles_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)

    success, detail = dashboard_api.git_pull_and_restart_supervisor()

    assert success is False
    assert "git pull" in detail.lower()


def test_restart_dashboard_service_uses_popen_not_run(monkeypatch):
    # Must never block waiting for a process that's about to kill the caller.
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: calls.append(cmd))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not call subprocess.run"))

    dashboard_api.restart_dashboard_service()

    assert calls == [["sudo", "systemctl", "restart", dashboard_api.DASHBOARD_SYSTEMD_UNIT]]


def test_load_profile_returns_a_plain_dict_not_a_pydantic_model(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "test_profile.json").write_text('{"name": "test_profile"}', encoding="utf-8")

    result = dashboard_api.load_profile("test_profile")
    assert isinstance(result, dict)
    assert result["name"] == "test_profile"


def test_list_profiles_delegates_to_profiles_module(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path)
    (tmp_path / "a.json").write_text('{"name": "a"}', encoding="utf-8")
    (tmp_path / "b.json").write_text('{"name": "b"}', encoding="utf-8")
    assert dashboard_api.list_profiles() == ["a", "b"]


def test_parse_guidelines_file_delegates_correctly():
    import train_loop
    content = train_loop.CATEGORY_HEADERS["content_positive"] + "\n- some rule\n"
    result = dashboard_api.parse_guidelines_file(content)
    assert result["content_positive"] == ["some rule"]


def test_tiktok_cookies_status_returns_a_tuple(tmp_path, monkeypatch):
    import tiktok_uploader
    monkeypatch.setattr(tiktok_uploader, "COOKIES_PATH", tmp_path / "cookies.json")
    ready, detail = dashboard_api.tiktok_cookies_status()
    assert ready is False
    assert isinstance(detail, str)


def test_default_hashtags_matches_tiktok_uploader():
    import tiktok_uploader
    assert dashboard_api.DEFAULT_HASHTAGS == tiktok_uploader.DEFAULT_HASHTAGS
