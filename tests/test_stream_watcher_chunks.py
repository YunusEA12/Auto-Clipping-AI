import subprocess
from pathlib import Path

import stream_watcher

# Captured before any test's autouse fixtures run (tests/conftest.py's _block_real_chunk_cleanup
# patches stream_watcher.cleanup_stale_chunks to raise, project-wide, so no test accidentally
# deletes real files under TEMP_DIR) — the tests below that genuinely exercise cleanup behavior
# restore this real reference locally rather than disabling the guard globally.
_real_cleanup_stale_chunks = stream_watcher.cleanup_stale_chunks


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


# --- cleanup_stale_chunks / run_periodic_chunk_cleanup (2026-08-25 incident: 181 accumulated
# .ts chunks / 24GB in temp/, oldest 5 days old — a real cycle's own _cleanup_cycle_temp_files()
# in auto_pilot.py cannot run if that process is killed abruptly (OOM, a restart mid-render).
# This is an independent, periodic, age-based safety net for exactly those interrupted cycles.

def _make_chunk(temp_dir, name, age_seconds, with_siblings=False):
    import os
    import time as time_mod
    path = temp_dir / f"{name}.ts"
    path.write_bytes(b"fake ts data")
    mtime = time_mod.time() - age_seconds
    os.utime(path, (mtime, mtime))
    if with_siblings:
        (temp_dir / f"{name}.wav").write_bytes(b"fake wav")
        (temp_dir / f"{name}_transcription.json").write_text("{}")
        (temp_dir / f"{name}_clips.json").write_text("{}")
    return path


def test_cleanup_deletes_chunks_older_than_max_age(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    old = _make_chunk(tmp_path, "live_chunk_x_1", age_seconds=25 * 3600)
    fresh = _make_chunk(tmp_path, "live_chunk_x_2", age_seconds=1 * 3600)

    deleted = stream_watcher.cleanup_stale_chunks(temp_dir=tmp_path, max_age_hours=24, min_free_disk_gb=0)

    assert deleted == 1
    assert not old.exists()
    assert fresh.exists()


def test_cleanup_removes_sibling_wav_and_json_files_too(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    old = _make_chunk(tmp_path, "live_chunk_x_1", age_seconds=25 * 3600, with_siblings=True)

    stream_watcher.cleanup_stale_chunks(temp_dir=tmp_path, max_age_hours=24, min_free_disk_gb=0)

    assert not old.exists()
    assert not (tmp_path / "live_chunk_x_1.wav").exists()
    assert not (tmp_path / "live_chunk_x_1_transcription.json").exists()
    assert not (tmp_path / "live_chunk_x_1_clips.json").exists()


def test_cleanup_never_touches_chunks_within_the_age_window(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    fresh = _make_chunk(tmp_path, "live_chunk_x_1", age_seconds=3600)
    deleted = stream_watcher.cleanup_stale_chunks(temp_dir=tmp_path, max_age_hours=24, min_free_disk_gb=0)
    assert deleted == 0
    assert fresh.exists()


def test_cleanup_disk_pressure_deletes_additional_chunks_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    # All three are within the 24h age window (so age-based deletion alone wouldn't touch
    # any of them), but simulated free disk space is below the floor -- the two OLDEST
    # (both older than the 30-minute safety floor) must go, the youngest must not.
    oldest = _make_chunk(tmp_path, "live_chunk_a", age_seconds=3 * 3600)
    middle = _make_chunk(tmp_path, "live_chunk_b", age_seconds=2 * 3600)
    youngest = _make_chunk(tmp_path, "live_chunk_c", age_seconds=60)  # under the 30min safety floor

    import itertools
    from collections import namedtuple
    DiskUsage = namedtuple("DiskUsage", "total used free")
    # Free space climbs back above the floor (20GB) once enough has been deleted: 5GB, 15GB,
    # 25GB on the 1st/2nd/3rd call (the 3rd, after deleting 2 chunks, clears the floor), then
    # stays at 25GB for the function's own final status-log call.
    free_sequence_gb = itertools.chain([5, 15, 25], itertools.repeat(25))
    monkeypatch.setattr(
        stream_watcher.shutil, "disk_usage",
        lambda path: DiskUsage(total=100 * 1024**3, used=0, free=int(next(free_sequence_gb) * 1e9)),
    )

    deleted = stream_watcher.cleanup_stale_chunks(temp_dir=tmp_path, max_age_hours=24, min_free_disk_gb=20)

    assert deleted == 2
    assert not oldest.exists()
    assert not middle.exists()
    assert youngest.exists()  # protected by RAW_CHUNK_MIN_SAFE_AGE_MINUTES regardless of disk pressure


def test_run_periodic_chunk_cleanup_self_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    monkeypatch.setattr(stream_watcher, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(stream_watcher, "CHUNK_CLEANUP_STATE_PATH", tmp_path / "state.json")
    old = _make_chunk(tmp_path, "live_chunk_x_1", age_seconds=25 * 3600)

    first = stream_watcher.run_periodic_chunk_cleanup()
    assert first == 1
    assert not old.exists()

    # A second call right after must be skipped (too soon) -- create a new stale chunk and
    # confirm it's NOT touched this time.
    other = _make_chunk(tmp_path, "live_chunk_x_2", age_seconds=25 * 3600)
    second = stream_watcher.run_periodic_chunk_cleanup()
    assert second is None
    assert other.exists()


def test_run_periodic_chunk_cleanup_force_bypasses_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    monkeypatch.setattr(stream_watcher, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(stream_watcher, "CHUNK_CLEANUP_STATE_PATH", tmp_path / "state.json")
    stream_watcher.run_periodic_chunk_cleanup()  # establishes last_run_at

    old = _make_chunk(tmp_path, "live_chunk_x_1", age_seconds=25 * 3600)
    deleted = stream_watcher.run_periodic_chunk_cleanup(force=True)

    assert deleted == 1
    assert not old.exists()


def test_cleanup_missing_temp_dir_returns_zero_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(stream_watcher, "cleanup_stale_chunks", _real_cleanup_stale_chunks)
    missing = tmp_path / "does_not_exist"
    assert stream_watcher.cleanup_stale_chunks(temp_dir=missing) == 0


def test_orchestrator_style_call_is_blocked_by_default(monkeypatch):
    # The exact shape of the live incident this whole isolation exists for: calling
    # stream_watcher.run_periodic_chunk_cleanup() (as orchestrator.py's main loop does, every
    # iteration) WITHOUT locally restoring the real cleanup_stale_chunks() must raise, not
    # silently touch the real TEMP_DIR.
    import pytest
    with pytest.raises(Exception):
        stream_watcher.run_periodic_chunk_cleanup(force=True)
