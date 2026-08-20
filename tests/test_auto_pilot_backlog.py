"""2026-08-21: found in a production health-check audit -- a clip that survived Phase 3's
critic purge in a past cycle but never made it into uploaded_clips/ (a crashed process, a VPS
restart mid-cycle, or an upload attempt that failed and outlived its own cycle) was invisible
to every later cycle forever for an auto_upload+publish streamer: run_deployment_phase() only
ever sees the current cycle's own `survivors`, and purge_old_local_only_clips() (the only
other thing that ever touches output/) deliberately skips this exact configuration (see its
own comment). find_backlog_clips() closes that gap; these tests cover it directly, plus the
_persist_reward_score() write it depends on to tell "evaluated, just never uploaded" apart
from "rendered but never reached scoring"."""

import json

import pytest

import auto_pilot
import upload_manager


@pytest.fixture(autouse=True)
def _stub_youtube_leg(monkeypatch):
    # Same stub as test_auto_pilot_deployment_gating.py -- run_deployment_phase() dispatches
    # to YouTube too, which has its own real pacing sleep between platforms; not what this
    # file is testing, so it's stubbed out rather than exercised here.
    monkeypatch.setattr(upload_manager.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        upload_manager, "_upload_to_youtube",
        lambda *a, **k: upload_manager.YouTubeOutcome(attempted=False, success=False, detail="stubbed in test"),
    )


def _write_render(output_dir, name, sidecar_data):
    output_dir.mkdir(parents=True, exist_ok=True)
    mp4 = output_dir / f"{name}.mp4"
    mp4.write_bytes(b"fake video")
    sidecar = output_dir / f"{name}.json"
    sidecar.write_text(json.dumps(sidecar_data), encoding="utf-8")
    return mp4


# --- find_backlog_clips ------------------------------------------------------------------

def test_backlog_clip_with_reward_score_is_picked_up(tmp_path):
    mp4 = _write_render(tmp_path, "clip_1_Old", {"title": "Old", "reward_score": 4})

    backlog = auto_pilot.find_backlog_clips(tmp_path, exclude=set())

    assert backlog == [({"title": "Old", "reward_score": 4}, mp4, 4)]


def test_backlog_clip_without_reward_score_is_skipped(tmp_path):
    # No reward_score in the sidecar means we can't prove this clip ever passed Phase 3 --
    # its process may have died between rendering and scoring. Must not be force-uploaded.
    _write_render(tmp_path, "clip_1_Unscored", {"title": "Unscored"})

    backlog = auto_pilot.find_backlog_clips(tmp_path, exclude=set())

    assert backlog == []


def test_backlog_excludes_paths_already_handled_this_cycle(tmp_path):
    mp4 = _write_render(tmp_path, "clip_1_Fresh", {"title": "Fresh", "reward_score": 2})

    backlog = auto_pilot.find_backlog_clips(tmp_path, exclude={mp4})

    assert backlog == []


def test_backlog_skips_missing_sidecar_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "orphan.mp4").write_bytes(b"fake")

    assert auto_pilot.find_backlog_clips(tmp_path, exclude=set()) == []


def test_backlog_skips_corrupt_sidecar_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "clip_1_Bad.mp4").write_bytes(b"fake")
    (tmp_path / "clip_1_Bad.json").write_text("{not valid json", encoding="utf-8")

    assert auto_pilot.find_backlog_clips(tmp_path, exclude=set()) == []


def test_backlog_missing_output_dir_is_empty(tmp_path):
    assert auto_pilot.find_backlog_clips(tmp_path / "does_not_exist", exclude=set()) == []


def test_backlog_returns_multiple_eligible_clips(tmp_path):
    mp4_a = _write_render(tmp_path, "clip_1_A", {"title": "A", "reward_score": 1})
    mp4_b = _write_render(tmp_path, "clip_2_B", {"title": "B", "reward_score": 5})

    backlog = auto_pilot.find_backlog_clips(tmp_path, exclude=set())

    assert {path for _, path, _ in backlog} == {mp4_a, mp4_b}


# --- _persist_reward_score ----------------------------------------------------------------

def test_persist_reward_score_merges_into_existing_sidecar(tmp_path):
    mp4 = _write_render(tmp_path, "clip_1_X", {"title": "X", "description": "desc"})

    auto_pilot._persist_reward_score(mp4, 7)

    saved = json.loads((tmp_path / "clip_1_X.json").read_text(encoding="utf-8"))
    assert saved["title"] == "X"
    assert saved["description"] == "desc"
    assert saved["reward_score"] == 7


def test_persist_reward_score_is_noop_when_score_is_none(tmp_path):
    mp4 = _write_render(tmp_path, "clip_1_Y", {"title": "Y"})

    auto_pilot._persist_reward_score(mp4, None)

    saved = json.loads((tmp_path / "clip_1_Y.json").read_text(encoding="utf-8"))
    assert "reward_score" not in saved


def test_persist_reward_score_does_not_raise_on_missing_sidecar(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    mp4 = tmp_path / "clip_1_NoSidecar.mp4"
    mp4.write_bytes(b"fake")

    auto_pilot._persist_reward_score(mp4, 3)  # must not raise

    assert not (tmp_path / "clip_1_NoSidecar.json").exists()


# --- run_deployment_phase merged with backlog (end-to-end through the real upload path,
# same pattern as test_auto_pilot_deployment_gating.py) ------------------------------------

def test_backlog_and_fresh_survivors_both_get_uploaded(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )

    fresh_path = tmp_path / "clip_1_Fresh.mp4"
    fresh_path.write_bytes(b"fake video bytes")
    fresh_clip = {"title": "Fresh", "description": "d", "hashtags": ["#fyp"], "viral_score": 8}

    backlog_path = _write_render(tmp_path, "clip_2_Backlog", {
        "title": "Backlog", "description": "d2", "hashtags": ["#fyp"], "reward_score": 3,
    })
    backlog = auto_pilot.find_backlog_clips(tmp_path, exclude={fresh_path})
    assert len(backlog) == 1

    uploaded, failed = auto_pilot.run_deployment_phase(
        [(fresh_clip, fresh_path, 5)] + backlog, publish=True,
    )

    assert uploaded == 2
    assert failed == 0
    assert (tmp_path / "uploaded_clips" / "clip_1_Fresh.mp4").exists()
    assert (tmp_path / "uploaded_clips" / "clip_2_Backlog.mp4").exists()


def test_backlog_batch_limit_matches_batch_size_max():
    # Guards the deliberate choice to cap backlog per cycle the same way fresh batches are
    # capped -- not a tautology check, a change to either constant without updating the other
    # would silently un-cap (or over-cap) backlog relative to the documented intent.
    assert auto_pilot.BACKLOG_BATCH_LIMIT == auto_pilot.BATCH_SIZE_MAX
