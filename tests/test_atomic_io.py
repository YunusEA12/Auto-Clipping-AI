import json
from pathlib import Path

import pytest

import atomic_io


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
