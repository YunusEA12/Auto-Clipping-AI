import json
import platform

import pytest

import atomic_io


# --- secure_file_permissions (M-07) -------------------------------------------------------

def test_secure_file_permissions_never_raises_on_a_real_file(tmp_path):
    target = tmp_path / "cookies.json"
    target.write_text("[]", encoding="utf-8")
    atomic_io.secure_file_permissions(target)  # must not raise
    assert target.read_text(encoding="utf-8") == "[]"  # content untouched


def test_secure_file_permissions_never_raises_on_a_missing_file(tmp_path):
    # Best-effort: a permission-hardening failure must never propagate and block the caller
    # from having written the file at all.
    atomic_io.secure_file_permissions(tmp_path / "does_not_exist.json")


@pytest.mark.skipif(platform.system() == "Windows", reason="chmod semantics are POSIX-specific")
def test_secure_file_permissions_restricts_mode_on_posix(tmp_path):
    target = tmp_path / "cookies.json"
    target.write_text("[]", encoding="utf-8")
    atomic_io.secure_file_permissions(target)
    assert (target.stat().st_mode & 0o777) == 0o600


def test_atomic_write_text_creates_file(tmp_path):
    target = tmp_path / "state.txt"
    atomic_io.atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_overwrites_existing_content(tmp_path):
    target = tmp_path / "state.txt"
    target.write_text("old", encoding="utf-8")
    atomic_io.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "state.json"
    atomic_io.atomic_write_json(target, {"a": 1})
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_atomic_write_json_roundtrip(tmp_path):
    target = tmp_path / "state.json"
    data = {"clips": [{"title": "a"}, {"title": "b"}]}
    atomic_io.atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8")) == data


def test_failed_write_does_not_touch_original(tmp_path, monkeypatch):
    target = tmp_path / "state.txt"
    target.write_text("original", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io.os, "replace", boom)

    with pytest.raises(OSError):
        atomic_io.atomic_write_text(target, "new content")

    # The failed write must not have truncated or replaced the original file — this is the
    # whole point of write-to-temp-then-replace over a direct open(path, "w").
    assert target.read_text(encoding="utf-8") == "original"
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []
