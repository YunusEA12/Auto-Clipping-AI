"""The account switch is a one-shot, destructive operation run at a stressful moment, so the
things it must NOT do (lose the ledger, move repository content) are what is tested here."""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def switcher(tmp_path, monkeypatch):
    """Loads the script with REPO_ROOT pointed at a throwaway directory holding a realistic
    set of state files."""
    module = importlib.import_module("scripts.switch_tiktok_account")
    module = importlib.reload(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "_fleet_is_running", lambda: [])
    monkeypatch.setattr(module, "_is_git_tracked", lambda name: False)

    (tmp_path / "viral_memory.json").write_text(json.dumps({"old": "numbers"}), encoding="utf-8")
    (tmp_path / "ai_guidelines.txt").write_text("learned from throttled account", encoding="utf-8")
    (tmp_path / "optimization_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "upload_pacing_state.json").write_text(json.dumps({"tiktok": "2026-10-28T21:29:29+00:00"}), encoding="utf-8")
    (tmp_path / "upload_ledger.json").write_text(json.dumps({"hash": {"tiktok": {"status": "done"}}}), encoding="utf-8")
    (tmp_path / "cookies.json").write_text("[]", encoding="utf-8")
    return module, tmp_path


def _run(module, *argv):
    sys.argv = ["switch_tiktok_account.py", *argv]
    return module.main()


def test_ledger_and_cookies_survive_the_switch(switcher):
    """Losing the ledger would re-upload already-posted clips to the new account as duplicate
    content — the exact thing most likely to get a fresh account throttled again."""
    module, root = switcher
    assert _run(module) == 0
    assert json.loads((root / "upload_ledger.json").read_text(encoding="utf-8"))["hash"]["tiktok"]["status"] == "done"
    assert (root / "cookies.json").exists()


def test_learned_state_is_archived_not_destroyed(switcher):
    module, root = switcher
    assert _run(module) == 0
    assert not (root / "viral_memory.json").exists()
    archived = list(root.glob("backup_alter_account/*/viral_memory.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text(encoding="utf-8")) == {"old": "numbers"}


def test_pacing_clock_is_cleared(switcher):
    """A stale pacing timestamp would make the new account sit out the old one's interval."""
    module, root = switcher
    assert _run(module) == 0
    assert not (root / "upload_pacing_state.json").exists()


def test_dry_run_changes_nothing(switcher):
    module, root = switcher
    assert _run(module, "--dry-run") == 0
    assert (root / "viral_memory.json").exists()
    assert (root / "upload_pacing_state.json").exists()
    assert not list(root.glob("backup_alter_account/*/*"))


def test_git_tracked_files_are_left_alone(switcher, monkeypatch):
    """Repository content must never be moved out of the checkout by an account switch."""
    module, root = switcher
    monkeypatch.setattr(module, "_is_git_tracked", lambda name: name == "viral_memory.json")
    assert _run(module) == 0
    assert (root / "viral_memory.json").exists()
    assert not (root / "ai_guidelines.txt").exists()


def test_refuses_to_run_while_the_fleet_is_up(switcher, monkeypatch):
    """A live auto_pilot would write the archived files straight back."""
    module, root = switcher
    monkeypatch.setattr(module, "_fleet_is_running", lambda: ["auto_pilot.py (pid 123)"])
    assert _run(module) == 1
    assert (root / "viral_memory.json").exists()


def test_force_overrides_the_running_fleet_guard(switcher, monkeypatch):
    module, root = switcher
    monkeypatch.setattr(module, "_fleet_is_running", lambda: ["auto_pilot.py (pid 123)"])
    assert _run(module, "--force") == 0
    assert not (root / "viral_memory.json").exists()


def test_clear_output_parks_clips_instead_of_deleting(switcher):
    module, root = switcher
    (root / "output").mkdir()
    (root / "output" / "clip_a.mp4").write_bytes(b"fake")
    assert _run(module, "--clear-output") == 0
    assert not (root / "output" / "clip_a.mp4").exists()
    assert len(list(root.glob("backup_alter_account/*/output/clip_a.mp4"))) == 1
