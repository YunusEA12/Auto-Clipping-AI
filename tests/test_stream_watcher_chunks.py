import subprocess
from pathlib import Path

import stream_watcher


class FakePopen:
    def __init__(self, *a, **k):
        self.stdout = None
        self.stderr = None

    def wait(self, timeout=None):
        pass

    def kill(self):
        pass


def _patch_subprocesses(monkeypatch, tmp_path):
    monkeypatch.setattr(stream_watcher, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(stream_watcher, "resolve_streamlink_path", lambda: "streamlink")
    monkeypatch.setattr(stream_watcher.subprocess, "Popen", lambda *a, **k: FakePopen())

    def fake_run(cmd, **kwargs):
        # cmd's last element is the ffmpeg output path — write a stand-in file so
        # record_stream_chunk()'s own existence/size check passes.
        Path(cmd[-1]).write_bytes(b"fake ts data")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(stream_watcher.subprocess, "run", fake_run)


# --- record_stream_chunk streamer_slug (2026-08-18: H-14 — two streamers whose auto_pilot.py
# cycles start a recording within the same wall-clock second used to collide on an identical
# temp/live_chunk_<timestamp>.ts filename) --------------------------------------------------

def test_chunk_filename_includes_streamer_slug_when_given(tmp_path, monkeypatch):
    _patch_subprocesses(monkeypatch, tmp_path)
    result = stream_watcher.record_stream_chunk("https://twitch.tv/x", duration=5, streamer_slug="eliasn97")
    assert result.parent == tmp_path
    assert result.name.startswith("live_chunk_eliasn97_")
    assert result.name.endswith(".ts")


def test_chunk_filename_unchanged_without_streamer_slug(tmp_path, monkeypatch):
    _patch_subprocesses(monkeypatch, tmp_path)
    result = stream_watcher.record_stream_chunk("https://twitch.tv/x", duration=5)
    assert result.name.startswith("live_chunk_")
    assert "eliasn97" not in result.name


def test_two_streamers_same_second_get_different_filenames(tmp_path, monkeypatch):
    _patch_subprocesses(monkeypatch, tmp_path)
    monkeypatch.setattr(stream_watcher.time, "time", lambda: 1000.0)  # same instant for both
    path_a = stream_watcher.record_stream_chunk("https://twitch.tv/a", duration=5, streamer_slug="eliasn97")
    path_b = stream_watcher.record_stream_chunk("https://twitch.tv/b", duration=5, streamer_slug="papaplatte")
    assert path_a != path_b
