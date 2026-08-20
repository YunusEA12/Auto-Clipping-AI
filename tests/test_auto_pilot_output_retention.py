"""2026-08-21: found in a production health-check audit -- output/<streamer>/ for a streamer
configured auto_upload=True, publish=False was never touched by anything (not low-scoring, so
purge_low_scoring_clips() leaves it; run_deployment_phase() is never called in this
configuration) -- clips accumulated forever with zero eviction, eventually filling the VPS's
only writable partition under systemd's ProtectSystem=strict."""

import os
import time
from pathlib import Path

import auto_pilot


def _write_clip(output_dir: Path, name: str, age_days: float) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    mp4 = output_dir / f"{name}.mp4"
    json_sidecar = output_dir / f"{name}.json"
    mp4.write_bytes(b"fake video")
    json_sidecar.write_text("{}", encoding="utf-8")
    mtime = time.time() - age_days * 86400
    os.utime(mp4, (mtime, mtime))
    os.utime(json_sidecar, (mtime, mtime))
    return mp4


def test_deletes_clips_older_than_retention_window(tmp_path):
    _write_clip(tmp_path, "old_clip", age_days=auto_pilot.OUTPUT_RETENTION_DAYS + 1)

    deleted = auto_pilot.purge_old_local_only_clips(tmp_path)

    assert deleted == 1
    assert not (tmp_path / "old_clip.mp4").exists()
    assert not (tmp_path / "old_clip.json").exists()


def test_keeps_clips_within_retention_window(tmp_path):
    _write_clip(tmp_path, "fresh_clip", age_days=1)

    deleted = auto_pilot.purge_old_local_only_clips(tmp_path)

    assert deleted == 0
    assert (tmp_path / "fresh_clip.mp4").exists()
    assert (tmp_path / "fresh_clip.json").exists()


def test_clip_just_under_the_retention_window_is_kept(tmp_path):
    # Comfortably under the window (not exactly at the boundary -- two time.time() calls a
    # moment apart make "exactly at the cutoff" inherently flaky to assert on).
    _write_clip(tmp_path, "almost_old_clip", age_days=auto_pilot.OUTPUT_RETENTION_DAYS - 1)

    deleted = auto_pilot.purge_old_local_only_clips(tmp_path)

    assert deleted == 0
    assert (tmp_path / "almost_old_clip.mp4").exists()


def test_missing_json_sidecar_does_not_block_mp4_deletion(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    mp4 = tmp_path / "orphan.mp4"
    mp4.write_bytes(b"fake")
    old = time.time() - (auto_pilot.OUTPUT_RETENTION_DAYS + 1) * 86400
    os.utime(mp4, (old, old))
    # Deliberately no orphan.json written -- must still delete the mp4 without raising.

    deleted = auto_pilot.purge_old_local_only_clips(tmp_path)

    assert deleted == 1
    assert not mp4.exists()


def test_missing_output_dir_is_a_no_op(tmp_path):
    assert auto_pilot.purge_old_local_only_clips(tmp_path / "does_not_exist") == 0


def test_custom_retention_days_respected(tmp_path):
    _write_clip(tmp_path, "clip_a", age_days=5)

    assert auto_pilot.purge_old_local_only_clips(tmp_path, retention_days=10) == 0
    assert auto_pilot.purge_old_local_only_clips(tmp_path, retention_days=3) == 1


def test_only_deletes_mp4_files_not_unrelated_json(tmp_path):
    # A .json with no matching .mp4 (e.g. a leftover from something else) must be left alone
    # -- this function only ever walks *.mp4 and derives the sidecar from that, never the
    # reverse.
    tmp_path.mkdir(parents=True, exist_ok=True)
    lonely_json = tmp_path / "unrelated.json"
    lonely_json.write_text("{}", encoding="utf-8")
    old = time.time() - (auto_pilot.OUTPUT_RETENTION_DAYS + 1) * 86400
    os.utime(lonely_json, (old, old))

    assert auto_pilot.purge_old_local_only_clips(tmp_path) == 0
    assert lonely_json.exists()
