import json
from pathlib import Path

import pytest

from autoclip import runtime
from autoclip.tools import migrate_runtime as migration


def test_migration_preserves_ledger_quota_and_credentials_and_translates_paths(tmp_path):
    original = {
        "upload_ledger.json": b'{"hash":{"tiktok":{"status":"done"}}}',
        "upload_pacing_state.json": b'{"tiktok_daily":{"count":3}}',
        "cookies.json": b'[{"name":"sessionid","value":"private-fixture"}]',
    }
    for name, data in original.items():
        (tmp_path / name).write_bytes(data)
    video = tmp_path / "output/creator/clip.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"original-render")
    state = tmp_path / "agent_state_creator.json"
    state.write_text(json.dumps({"video_path": str(video), "last_clips_path": "temp/example_clips.json"}))
    (tmp_path / "temp").mkdir()
    (tmp_path / "temp/example_clips.json").write_text("{}")
    result = migration.migrate(tmp_path, check_processes=False)
    assert result["status"] == "complete"
    for name, data in original.items():
        assert runtime.path(name, home=tmp_path).read_bytes() == data
        assert not (tmp_path / name).exists()
    assert (tmp_path / "var/output/creator/clip.mp4").read_bytes() == b"original-render"
    updated = json.loads(runtime.path(state.name, home=tmp_path).read_text())
    assert updated["video_path"] == (tmp_path / "var/output/creator/clip.mp4").as_posix()
    assert updated["last_clips_path"] == "var/temp/example_clips.json"
    assert migration.migrate(tmp_path, check_processes=False)["status"] == "already_migrated"


def test_collision_is_detected_before_any_file_moves(tmp_path):
    (tmp_path / "cookies.json").write_text("[]")
    (tmp_path / "upload_ledger.json").write_text("{}")
    destination = runtime.path("upload_ledger.json", home=tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_text('{"do_not_overwrite":{}}')
    with pytest.raises(FileExistsError):
        migration.migrate(tmp_path, check_processes=False)
    assert (tmp_path / "cookies.json").exists()
    assert (tmp_path / "upload_ledger.json").exists()
    assert "do_not_overwrite" in destination.read_text()


def test_dry_run_changes_nothing(tmp_path):
    (tmp_path / "cookies.json").write_text("[]")
    assert len(migration.plan(tmp_path)) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cookies.json"]


def test_failed_move_rolls_back_completed_moves(tmp_path, monkeypatch):
    for name in ("cookies.json", "fleet_control.json"):
        (tmp_path / name).write_text("{}")
    original_replace = Path.replace

    def replace(path, target):
        if path == tmp_path / "fleet_control.json":
            raise OSError("simulated failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="simulated"):
        migration.migrate(tmp_path, check_processes=False)
    assert (tmp_path / "cookies.json").read_text() == "{}"
    assert (tmp_path / "fleet_control.json").read_text() == "{}"
    assert not runtime.path("cookies.json", home=tmp_path).exists()


def test_bootstrap_does_not_overwrite_custom_profiles(tmp_path):
    runtime.ensure_directories(home=tmp_path)
    custom = runtime.path("profiles", home=tmp_path) / "default_streamer.json"
    custom.write_text('{"custom":true}')
    runtime.ensure_directories(home=tmp_path)
    assert json.loads(custom.read_text()) == {"custom": True}


def test_runtime_rejects_traversal():
    with pytest.raises(ValueError):
        runtime.path("agent_state_../../secrets.json")
