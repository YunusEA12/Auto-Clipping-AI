"""Safety-model correction, 2026-08-18: TikTok has no draft-save action — an unpublished,
unclicked upload is discarded by TikTok, not saved anywhere (disproven directly by the
account owner checking TikTok Studio and the mobile app after a real test; an earlier
version of this codebase wrongly assumed the opposite). Phase 5 (Deployment) must therefore
require an explicit publish=True, not just auto_upload=True, or it silently wastes every
upload attempt for nothing."""

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
