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


# --- Platform-state persistence across TikTok-unconfirmed retries (2026-08-21) — found live:
# a single real clip whose TikTok confirmation kept flaking across 3 cycles ended up with 3
# separate real YouTube videos and 3 separate real Instagram posts for identical content,
# because each retry re-ran upload_clip_everywhere() from scratch with no memory of which
# platforms had already genuinely succeeded on an earlier attempt at the same still-pending
# clip. -----------------------------------------------------------------------------------

def test_youtube_and_instagram_not_reattempted_after_tiktok_unconfirmed_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")

    tiktok_calls = []

    def fake_tiktok(*a, **k):
        tiktok_calls.append(1)
        confirmed = len(tiktok_calls) >= 2  # unconfirmed on the first attempt, confirmed on the second
        return auto_pilot.tiktok_uploader.UploadOutcome(success=True, confirmed=confirmed)

    monkeypatch.setattr(auto_pilot.tiktok_uploader, "try_upload_clip", fake_tiktok)

    youtube_calls = []

    def fake_youtube(*a, **k):
        youtube_calls.append(1)
        return upload_manager.YouTubeOutcome(attempted=True, success=True, video_id="abc123")

    monkeypatch.setattr(upload_manager, "_upload_to_youtube", fake_youtube)
    monkeypatch.setattr(upload_manager.time, "sleep", lambda _s: None)

    instagram_calls = []

    def fake_instagram(*a, **k):
        instagram_calls.append(1)
        return upload_manager.instagram_uploader.UploadOutcome(success=True, confirmed=True)

    monkeypatch.setattr(upload_manager.instagram_uploader, "try_upload_clip", fake_instagram)

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    # Cycle 1: TikTok unconfirmed — clip stays in output/ for a retry, but YouTube/Instagram
    # already succeeded independently in this same call and that must be remembered.
    uploaded1, failed1 = auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True, instagram=True)
    assert (uploaded1, failed1) == (0, 1)
    assert output_path.exists()
    assert youtube_calls == [1]
    assert instagram_calls == [1]

    # Cycle 2 (a later loop iteration retrying the same still-pending clip): TikTok confirms
    # this time. Before the fix, this call would have re-uploaded to YouTube and Instagram a
    # second time; now it must reuse the already-recorded successful outcomes instead.
    uploaded2, failed2 = auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True, instagram=True)
    assert (uploaded2, failed2) == (1, 0)
    assert tiktok_calls == [1, 1]  # TikTok itself is retried — a separate, pre-existing risk
    assert youtube_calls == [1]  # NOT called a second time
    assert instagram_calls == [1]  # NOT called a second time

    sidecar = json.loads((tmp_path / "uploaded_clips" / "clip_1_Test.json").read_text(encoding="utf-8"))
    assert sidecar["youtube_uploaded"] is True
    assert sidecar["youtube_url"] == "https://youtu.be/abc123"
    assert sidecar["instagram_uploaded"] is True
    assert not output_path.with_suffix(".platform_state.json").exists()  # cleaned up once archived


def test_platform_state_also_persisted_when_tiktok_fails_outright(tmp_path, monkeypatch):
    """YouTube/Instagram run independently of TikTok's own result inside
    upload_clip_everywhere() — a hard TikTok failure (not just an unconfirmed click) must
    still persist any YouTube/Instagram success, or a retry after an outright TikTok failure
    would re-trigger the exact same duplicate-post bug this whole fix addresses."""
    monkeypatch.setattr(auto_pilot, "UPLOADED_CLIPS_DIR", tmp_path / "uploaded_clips")
    monkeypatch.setattr(
        auto_pilot.tiktok_uploader, "try_upload_clip",
        lambda *a, **k: auto_pilot.tiktok_uploader.UploadOutcome(success=False, confirmed=False),
    )
    monkeypatch.setattr(upload_manager.time, "sleep", lambda _s: None)
    instagram_calls = []
    monkeypatch.setattr(
        upload_manager.instagram_uploader, "try_upload_clip",
        lambda *a, **k: (instagram_calls.append(1), upload_manager.instagram_uploader.UploadOutcome(success=True, confirmed=True))[1],
    )

    output_path = tmp_path / "clip_1_Test.mp4"
    output_path.write_bytes(b"fake video bytes")
    clip = {"title": "Test", "description": "desc", "hashtags": ["#fyp"], "viral_score": 8}

    auto_pilot.run_deployment_phase([(clip, output_path, 3)], publish=True, instagram=True)

    assert instagram_calls == [1]
    skip_youtube, skip_instagram = auto_pilot._read_platform_state(output_path)
    assert skip_instagram is not None and skip_instagram.success is True
