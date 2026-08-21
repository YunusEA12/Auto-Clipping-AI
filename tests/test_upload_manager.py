"""2026-08-20: upload_manager.py dispatches one rendered clip to every configured platform
(TikTok via tiktok_uploader.py, YouTube Shorts via upload.py) instead of auto_pilot.py/
stream_watcher.py calling tiktok_uploader.py directly. Covers: the same `publish` flag
gating both platforms identically, the pacing delay between them, and a YouTube failure never
propagating past this module (mirrors tiktok_uploader.try_upload_clip()'s own
never-raises contract, one level up)."""

from pathlib import Path

import pytest
from googleapiclient.errors import HttpError

import tiktok_uploader
import upload as youtube_uploader
import upload_instagram_playwright as instagram_uploader
import upload_manager


class FakeHttpResponse:
    def __init__(self, status, reason=""):
        self.status = status
        # HttpError.__init__ -> _get_reason() unconditionally reads self.resp.reason before
        # ever looking at the JSON content, real googleapiclient responses always have this
        # (found in review, 2026-08-21: without it, HttpError's own constructor raised
        # AttributeError before ever producing a usable HttpError, and every test using this
        # fixture was silently passing for the wrong reason — any exception, not specifically
        # a correctly-detected HttpError, still made the assertions' `success is False` true).
        self.reason = reason


def _make_http_error(status, message=b"error"):
    return HttpError(resp=FakeHttpResponse(status), content=message)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Every test that reaches the delay path must not actually wait seconds.
    monkeypatch.setattr(upload_manager.time, "sleep", lambda _s: None)


@pytest.fixture
def fake_tiktok_success(monkeypatch):
    outcome = tiktok_uploader.UploadOutcome(success=True, confirmed=True)
    monkeypatch.setattr(tiktok_uploader, "try_upload_clip", lambda *a, **k: outcome)
    return outcome


@pytest.fixture
def fake_tiktok_failure(monkeypatch):
    outcome = tiktok_uploader.UploadOutcome(success=False, confirmed=False)
    monkeypatch.setattr(tiktok_uploader, "try_upload_clip", lambda *a, **k: outcome)
    return outcome


# --- publish=False gates BOTH platforms identically -------------------------------------------

def test_publish_false_skips_youtube_without_calling_it(monkeypatch, fake_tiktok_success):
    called = []
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: called.append(1))

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=False)

    assert called == []
    assert result.youtube.attempted is False
    assert result.youtube.success is False


def test_publish_false_still_calls_tiktok_which_no_ops_internally(monkeypatch):
    # tiktok_uploader.try_upload_clip() is itself the thing that no-ops on publish=False (see
    # its own module docstring) -- upload_manager must still call it, not skip it, so that
    # existing behavior (and tiktok_uploader's own logging of the no-op) is preserved.
    calls = []
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: calls.append(k) or tiktok_uploader.UploadOutcome(success=False, confirmed=False),
    )
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(AssertionError))

    upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=False)

    assert len(calls) == 1
    assert calls[0]["publish"] is False


# --- publish=True: both platforms actually attempted, with a delay between them ---------------

def test_publish_true_attempts_both_platforms(monkeypatch, fake_tiktok_success):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.tiktok.success is True
    assert result.youtube.attempted is True
    assert result.youtube.success is True
    assert result.youtube.video_id == "yt-video-id"
    assert result.youtube.url == "https://youtu.be/yt-video-id"


def test_publish_true_sleeps_between_platforms_within_configured_bounds(monkeypatch, fake_tiktok_success):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")
    sleep_calls = []
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: sleep_calls.append(s))

    upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert len(sleep_calls) == 1
    assert upload_manager.UPLOAD_DELAY_MIN_SECONDS <= sleep_calls[0] <= upload_manager.UPLOAD_DELAY_MAX_SECONDS


def test_publish_false_never_sleeps(monkeypatch, fake_tiktok_failure):
    sleep_calls = []
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: sleep_calls.append(s))

    upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=False)

    assert sleep_calls == []


# --- a YouTube failure never propagates past this module ---------------------------------------

def test_youtube_generic_exception_does_not_raise(monkeypatch, fake_tiktok_success):
    def boom(*a, **k):
        raise RuntimeError("network blip")
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.youtube.success is False
    assert "network blip" in result.youtube.detail
    # TikTok's own (successful) result must be unaffected by YouTube's failure.
    assert result.tiktok.success is True


def test_youtube_quota_http_error_does_not_raise(monkeypatch, fake_tiktok_success):
    def boom(*a, **k):
        raise _make_http_error(403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.youtube.success is False
    assert result.youtube.attempted is True


def test_youtube_non_quota_http_error_does_not_raise(monkeypatch, fake_tiktok_success):
    def boom(*a, **k):
        raise _make_http_error(500, b"server error")
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.youtube.success is False


# --- uploadLimitExceeded backoff (2026-08-21, found live: this specific channel-level daily
# cap error has HTTP status 400 and no "quota" substring in its message, so the existing
# quota_exceeded check never matched it — every upload attempt (first try and every automatic
# retry cycle, every few minutes) kept hammering the API with zero backoff) --------------------

UPLOAD_LIMIT_ERROR = (
    b'{"error": {"errors": [{"reason": "uploadLimitExceeded"}], '
    b'"message": "The user has exceeded the number of videos they may upload."}}'
)


def test_upload_limit_exceeded_records_backoff_and_does_not_raise(monkeypatch, tmp_path, fake_tiktok_success):
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")

    def boom(*a, **k):
        raise _make_http_error(400, UPLOAD_LIMIT_ERROR)
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.youtube.success is False
    assert result.youtube.attempted is True
    assert upload_manager._upload_limit_backoff_until() is not None


def test_second_attempt_during_backoff_skips_the_real_api_call(monkeypatch, tmp_path, fake_tiktok_success):
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise _make_http_error(400, UPLOAD_LIMIT_ERROR)
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    upload_manager.upload_clip_everywhere(Path("clip_1.mp4"), "Title", "desc", publish=True)
    assert len(calls) == 1  # first attempt: real call, hits the limit, records backoff

    result = upload_manager.upload_clip_everywhere(Path("clip_2.mp4"), "Title", "desc", publish=True)
    assert len(calls) == 1  # second attempt: skipped entirely, no new API call
    assert result.youtube.attempted is False
    assert result.youtube.success is False


def test_backoff_clears_after_the_window_elapses(monkeypatch, tmp_path, fake_tiktok_success):
    backoff_path = tmp_path / "backoff.json"
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", backoff_path)
    stale_seen_at = upload_manager.datetime.now(upload_manager.timezone.utc) - upload_manager.timedelta(
        minutes=upload_manager.UPLOAD_LIMIT_BACKOFF_MINUTES + 1
    )
    backoff_path.write_text(f'{{"seen_at": "{stale_seen_at.isoformat()}"}}', encoding="utf-8")

    assert upload_manager._upload_limit_backoff_until() is None

    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")
    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.youtube.attempted is True
    assert result.youtube.success is True


def test_quota_exceeded_and_upload_limit_exceeded_are_detected_distinctly(monkeypatch, tmp_path, fake_tiktok_success):
    # A 403 quotaExceeded must NOT be mistaken for the 400 uploadLimitExceeded case -- they
    # need different handling (quota resets are a Google Cloud project concern, not this
    # channel's own daily cap) and must not share the same backoff state.
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")

    def boom(*a, **k):
        raise _make_http_error(403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert upload_manager._upload_limit_backoff_until() is None


# --- MultiPlatformOutcome.any_success ----------------------------------------------------------

def test_any_success_true_when_only_tiktok_succeeds(monkeypatch, fake_tiktok_success):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)
    assert result.any_success is True


def test_any_success_true_when_only_youtube_succeeds(monkeypatch, fake_tiktok_failure):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)
    assert result.any_success is True


def test_any_success_false_when_both_fail(monkeypatch, fake_tiktok_failure):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)
    assert result.any_success is False


def test_any_success_false_when_tiktok_succeeds_but_unconfirmed(monkeypatch):
    # success=True but confirmed=False must NOT count as a real success (matches how
    # auto_pilot.py itself treats an unconfirmed click as a failure).
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: tiktok_uploader.UploadOutcome(success=True, confirmed=False),
    )
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)

    assert result.any_success is False


# --- add_background_sound passthrough (2026-08-19 feature, must survive this refactor) --------

def test_add_background_sound_flag_passed_through_to_tiktok(monkeypatch, fake_tiktok_success):
    calls = []
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: calls.append(k) or fake_tiktok_success,
    )
    upload_manager.upload_clip_everywhere(
        Path("clip.mp4"), "Title", "desc", publish=False, add_background_sound=False,
    )
    assert calls[0]["add_background_sound"] is False


# --- Instagram gating (2026-08-21) — a separate opt-in from publish itself, since
# upload_instagram_playwright.py's automation has never been verified against a live session
# (see that module's own docstring): instagram_enabled must default False and require BOTH
# publish=True and instagram_enabled=True before the real automation is ever touched --------

def test_instagram_not_attempted_when_disabled_even_with_publish_true(monkeypatch, fake_tiktok_success):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    called = []
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", lambda *a, **k: called.append(1))

    result = upload_manager.upload_clip_everywhere(Path("clip.mp4"), "Title", "desc", publish=True)  # instagram_enabled defaults False

    assert called == []
    assert result.instagram.attempted is False
    assert result.instagram.success is False


def test_instagram_not_attempted_when_publish_false_even_if_enabled(monkeypatch, fake_tiktok_success):
    called = []
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", lambda *a, **k: called.append(1))

    result = upload_manager.upload_clip_everywhere(
        Path("clip.mp4"), "Title", "desc", publish=False, instagram_enabled=True,
    )

    assert called == []
    assert result.instagram.attempted is False


def test_instagram_attempted_when_both_publish_and_enabled_true(monkeypatch, fake_tiktok_success):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=True),
    )

    result = upload_manager.upload_clip_everywhere(
        Path("clip.mp4"), "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.instagram.attempted is True
    assert result.instagram.success is True
    assert result.instagram.confirmed is True


def test_instagram_exception_does_not_raise_or_block_other_platforms(monkeypatch, fake_tiktok_success):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")

    def boom(*a, **k):
        raise RuntimeError("playwright blew up")
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(
        Path("clip.mp4"), "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.instagram.success is False
    assert "playwright blew up" in result.instagram.detail
    assert result.tiktok.success is True
    assert result.youtube.success is True


def test_any_success_true_when_only_instagram_succeeds(monkeypatch, fake_tiktok_failure):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=True),
    )

    result = upload_manager.upload_clip_everywhere(
        Path("clip.mp4"), "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.any_success is True


def test_any_success_false_when_instagram_succeeds_but_unconfirmed(monkeypatch, fake_tiktok_failure):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=False),
    )

    result = upload_manager.upload_clip_everywhere(
        Path("clip.mp4"), "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.any_success is False
