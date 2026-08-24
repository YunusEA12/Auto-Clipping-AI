"""Safety-model correction, 2026-08-18: TikTok has no draft-save action — an unpublished,
unclicked upload is discarded by TikTok, not saved anywhere (disproven directly by the
account owner checking TikTok Studio and the mobile app after a real test; an earlier
version of this codebase wrongly assumed the opposite). Phase 5 (Deployment) must therefore
require an explicit publish=True, not just auto_upload=True, or it silently wastes every
upload attempt for nothing."""

import json

import pytest

import auto_pilot
import upload_manager


@pytest.fixture(autouse=True)
def _stub_youtube_leg(monkeypatch):
    # run_deployment_phase() now also dispatches to YouTube via upload_manager.py
    # (2026-08-20) -- these tests are about TikTok's own confirmed/unconfirmed handling
    # specifically, so the YouTube leg (and its pacing delay) is stubbed out rather than
    # exercised here. See tests/test_upload_manager.py for YouTube-path coverage, and
    # tests/conftest.py for why an unmocked real call/sleep fails loudly instead of silently
    # doing something real (found live, 2026-08-20: these two tests originally uploaded a
    # real, public, garbage video to the account owner's actual YouTube channel).
    monkeypatch.setattr(upload_manager.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        upload_manager, "_upload_to_youtube",
        lambda *a, **k: upload_manager.YouTubeOutcome(attempted=False, success=False, detail="stubbed in test"),
    )


def test_deploy_requires_both_auto_upload_and_publish():
    assert auto_pilot.should_deploy(auto_upload=True, publish=True, survivors=["x"]) is True


def test_no_deploy_when_auto_upload_without_publish():
    # This is the exact bug this fix closes: auto_upload alone used to trigger a browser
    # upload that TikTok would just discard.
    assert auto_pilot.should_deploy(auto_upload=True, publish=False, survivors=["x"]) is False


def test_no_deploy_when_publish_without_auto_upload():
    assert auto_pilot.should_deploy(auto_upload=False, publish=True, survivors=["x"]) is False


def test_no_deploy_when_neither_set():
    assert auto_pilot.should_deploy(auto_upload=False, publish=False, survivors=["x"]) is False


def test_no_deploy_when_no_survivors_even_if_both_flags_set():
    assert auto_pilot.should_deploy(auto_upload=True, publish=True, survivors=[]) is False


# --- run_deployment_phase unconfirmed handling (2026-08-18: an unconfirmed upload used to
# be archived into uploaded_clips/ anyway, leaving a phantom "confirmed": false entry for a
# video TikTok never actually published, with nothing left in output/ for a retry — found
# live the same day a bogus confirmation signal made every real upload failure look locally
# successful) ---------------------------------------------------------------------------

def test_unconfirmed_upload_stays_in_output_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=False),
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    uploaded, failed = auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True)

    assert uploaded == 0
    assert failed == 1
    assert output_path.exists()  # left in place for a retry, not archived
    assert not (tmp_path / "uploaded_clips" / "clip_1_Test.mp4").exists()


def test_confirmed_upload_moves_to_uploaded_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    uploaded, failed = auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True)

    assert uploaded == 1
    assert failed == 0
    assert not output_path.exists()
    assert (tmp_path / "uploaded_clips" / "clip_1_Test.mp4").exists()


def test_streamer_name_recorded_in_sidecar_for_backlog_scoping(tmp_path, monkeypatch):
    # 2026-08-21: find_missing_youtube_uploads() uses this field to scope its retry scan to
    # just this streamer's own clips out of the shared uploaded_clips/ directory.
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True, streamer_name="alice")

    sidecar = json.loads((tmp_path / "uploaded_clips" / "clip_1_Test.json").read_text(encoding="utf-8"))
    assert sidecar["streamer_name"] == "alice"


def test_streamer_name_defaults_to_none_when_not_given(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True)

    sidecar = json.loads((tmp_path / "uploaded_clips" / "clip_1_Test.json").read_text(encoding="utf-8"))
    assert sidecar["streamer_name"] is None


# --- Instagram integration (2026-08-21) — a separate opt-in from publish itself, since
# upload_instagram_playwright.py's automation has never been verified against a live session
# (see that module's own docstring); defaults off for every streamer regardless of publish. ---

def test_instagram_not_attempted_when_flag_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )
    called = []
    monkeypatch.setattr(
        upload_manager.instagram_uploader, "try_upload_clip",
        lambda *a, **k: called.append(1),
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True)  # instagram defaults False

    assert called == []
    sidecar = json.loads((tmp_path / "uploaded_clips" / "clip_1_Test.json").read_text(encoding="utf-8"))
    assert sidecar["instagram_enabled"] is False
    assert sidecar["instagram_uploaded"] is False


def test_instagram_attempted_and_recorded_when_flag_set(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )
    monkeypatch.setattr(
        upload_manager.instagram_uploader, "try_upload_clip",
        lambda *a, **k: upload_manager.instagram_uploader.UploadOutcome(success=True, confirmed=True),
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True, instagram=True)

    sidecar = json.loads((tmp_path / "uploaded_clips" / "clip_1_Test.json").read_text(encoding="utf-8"))
    assert sidecar["instagram_enabled"] is True
    assert sidecar["instagram_uploaded"] is True


# --- Duplicate-upload protection (2026-08-21) now lives centrally in upload_manager.py/
# upload_ledger.py — see tests/test_upload_manager.py and tests/test_upload_ledger.py for that
# coverage. This file's own tests above stay focused on run_deployment_phase's archiving
# logic (confirmed vs. unconfirmed vs. failed) rather than duplicating dedup coverage here.


# --- 2026-08-24 dashboard-stall fix: per-clip heartbeat inside run_deployment_phase ------------
# See auto_pilot.run_deployment_phase()'s own comment for the incident this closes — a batch of
# several clips (each with its own pacing waits / Instagram retry backoff) could run past
# app.py's STALE_THRESHOLD_SECONDS between the ONE update_agent_state() call the caller makes
# before this loop starts and the next one after it (and any backlog sweeps) finish, falsely
# flagging a genuinely-busy agent "offline" on the dashboard.

def test_heartbeat_updates_once_per_clip_in_the_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(auto_pilot, "_agent_state_path_override", tmp_path / "agent_state_test.json")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )

    clips = []
    for i in range(1, 4):
        output_path = tmp_path / f"clip_{i}_Test.mp4"
        output_path.write_bytes(b"fake video bytes")
        clips.append(({"title": f"Test {i}", "description": "desc", "hashtags": ["#fyp"]}, output_path, 3))

    actions_seen = []
    real_update = auto_pilot.update_agent_state

    def _spy(**updates):
        if "current_action" in updates:
            actions_seen.append(updates["current_action"])
        return real_update(**updates)
    monkeypatch.setattr(auto_pilot, "update_agent_state", _spy)

    auto_pilot.run_deployment_phase(clips, publish=True)

    assert len(actions_seen) == 3  # one heartbeat write per clip, not just once for the batch
    assert "1/3" in actions_seen[0] and "Test 1" in actions_seen[0]
    assert "3/3" in actions_seen[2] and "Test 3" in actions_seen[2]
