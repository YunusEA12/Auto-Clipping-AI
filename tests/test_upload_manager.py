"""2026-08-20: upload_manager.py dispatches one rendered clip to every configured platform
(TikTok via tiktok_uploader.py, YouTube Shorts via upload.py) instead of auto_pilot.py/
stream_watcher.py calling tiktok_uploader.py directly. Covers: the same `publish` flag
gating both platforms identically, the pacing delay between them, and a YouTube failure never
propagating past this module (mirrors tiktok_uploader.try_upload_clip()'s own
never-raises contract, one level up).

2026-08-21: every test below now uploads a real (tiny, fake-content) file on disk rather than
a bare Path("clip.mp4") that never existed — upload_ledger.compute_content_hash() reads the
file's actual bytes as part of every real dedup check inside _upload_to_tiktok()/
_upload_to_youtube()/_upload_to_instagram(), so a nonexistent path now raises before any of
the mocked platform functions are ever reached. See test_upload_ledger.py for the ledger
module's own unit coverage, and the "Duplicate-upload protection" section at the bottom of
this file for coverage of the dedup behavior itself."""

from pathlib import Path

import pytest
from googleapiclient.errors import HttpError

import tiktok_uploader
import upload as youtube_uploader
import upload_instagram_playwright as instagram_uploader
import upload_ledger
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
def clip_path(tmp_path) -> Path:
    """A real file on disk — upload_ledger.compute_content_hash() reads actual bytes, so a
    bare Path("clip.mp4") that was never written would raise FileNotFoundError before any
    mocked platform function is ever reached."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake video bytes")
    return path


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

def test_publish_false_skips_youtube_without_calling_it(monkeypatch, fake_tiktok_success, clip_path):
    called = []
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: called.append(1))

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=False)

    assert called == []
    assert result.youtube.attempted is False
    assert result.youtube.success is False


def test_publish_false_still_calls_tiktok_which_no_ops_internally(monkeypatch, clip_path):
    # tiktok_uploader.try_upload_clip() is itself the thing that no-ops on publish=False (see
    # its own module docstring) -- upload_manager must still call it, not skip it, so that
    # existing behavior (and tiktok_uploader's own logging of the no-op) is preserved.
    calls = []
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: calls.append(k) or tiktok_uploader.UploadOutcome(success=False, confirmed=False),
    )
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(AssertionError))

    upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=False)

    assert len(calls) == 1
    assert calls[0]["publish"] is False


# --- publish=True: both platforms actually attempted, with a delay between them ---------------

def test_publish_true_attempts_both_platforms(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.tiktok.success is True
    assert result.youtube.attempted is True
    assert result.youtube.success is True
    assert result.youtube.video_id == "yt-video-id"
    assert result.youtube.url == "https://youtu.be/yt-video-id"


def test_publish_true_sleeps_between_platforms_within_configured_bounds(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")
    sleep_calls = []
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: sleep_calls.append(s))

    upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert len(sleep_calls) == 1
    assert upload_manager.UPLOAD_DELAY_MIN_SECONDS <= sleep_calls[0] <= upload_manager.UPLOAD_DELAY_MAX_SECONDS


def test_publish_false_never_sleeps(monkeypatch, fake_tiktok_failure, clip_path):
    sleep_calls = []
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: sleep_calls.append(s))

    upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=False)

    assert sleep_calls == []


# --- 2026-08-24/25: global per-platform daily-quota pacing gate --------------------------------
# See upload_manager._try_reserve_upload_pacing_slot()'s own docstring for why this exists (the
# 2026-08-23 incident: TikTok/YouTube's daily quotas were front-loaded into the first half of
# the day) and why it's a non-blocking check-and-reserve rather than a sleep (2026-08-25 fix: a
# blocking sleep here stalled the entire per-streamer capture loop, not just the upload step —
# found live the next day). Isolated from the real state file by tests/conftest.py's autouse
# _isolated_upload_pacing_state fixture.

def test_reserve_never_sleeps_and_grants_the_first_ever_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(upload_manager, "UPLOAD_PACING_STATE_PATH", tmp_path / "pacing.json")
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))

    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 300) is True


def test_second_attempt_within_interval_is_denied_not_delayed(monkeypatch, tmp_path):
    monkeypatch.setattr(upload_manager, "UPLOAD_PACING_STATE_PATH", tmp_path / "pacing.json")
    monkeypatch.setattr(upload_manager, "TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS", 100)
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))

    fake_now = upload_manager.datetime(2026, 1, 1, tzinfo=upload_manager.timezone.utc)

    class _FakeDateTime(upload_manager.datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now

    monkeypatch.setattr(upload_manager, "datetime", _FakeDateTime)

    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 100) is True  # first: granted

    fake_now = fake_now + upload_manager.timedelta(seconds=40)  # only 40s of a 100s interval
    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 100) is False  # denied, no sleep

    fake_now = fake_now + upload_manager.timedelta(seconds=61)  # now 101s since the granted slot
    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 100) is True  # interval elapsed


def test_pacing_is_tracked_independently_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(upload_manager, "UPLOAD_PACING_STATE_PATH", tmp_path / "pacing.json")
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))

    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 300) is True
    assert upload_manager._try_reserve_upload_pacing_slot("youtube", 300) is True  # a fresh platform


def test_zero_interval_always_grants(monkeypatch, tmp_path):
    monkeypatch.setattr(upload_manager, "UPLOAD_PACING_STATE_PATH", tmp_path / "pacing.json")
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")))

    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 0) is True
    assert upload_manager._try_reserve_upload_pacing_slot("tiktok", 0) is True


def test_denied_slot_is_not_a_regression_to_blocking_the_caller(monkeypatch, tmp_path, fake_tiktok_success, clip_path):
    """The specific bug this fixes: upload_clip_everywhere() (called synchronously, once per
    clip, from auto_pilot.py's single-threaded per-streamer cycle) must return promptly even
    when the pacing slot is denied — never block the caller waiting for it to free up."""
    monkeypatch.setattr(upload_manager, "UPLOAD_PACING_STATE_PATH", tmp_path / "pacing.json")
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")
    monkeypatch.setattr(upload_manager, "TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS", 9999)
    upload_manager._try_reserve_upload_pacing_slot("tiktok", 9999)  # consume the only slot for a long time

    sleep_calls = []
    monkeypatch.setattr(upload_manager.time, "sleep", lambda s: sleep_calls.append(s))

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.tiktok.success is False  # denied this cycle, not attempted
    assert all(s <= upload_manager.UPLOAD_DELAY_MAX_SECONDS for s in sleep_calls)  # only the platform stagger, never a 9999s pacing wait


# --- a YouTube failure never propagates past this module ---------------------------------------

def test_youtube_generic_exception_does_not_raise(monkeypatch, fake_tiktok_success, clip_path):
    def boom(*a, **k):
        raise RuntimeError("network blip")
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.youtube.success is False
    assert "network blip" in result.youtube.detail
    # TikTok's own (successful) result must be unaffected by YouTube's failure.
    assert result.tiktok.success is True


def test_youtube_quota_http_error_does_not_raise(monkeypatch, fake_tiktok_success, clip_path):
    def boom(*a, **k):
        raise _make_http_error(403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.youtube.success is False
    assert result.youtube.attempted is True


def test_youtube_non_quota_http_error_does_not_raise(monkeypatch, fake_tiktok_success, clip_path):
    def boom(*a, **k):
        raise _make_http_error(500, b"server error")
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.youtube.success is False


# --- uploadLimitExceeded backoff (2026-08-21, found live: this specific channel-level daily
# cap error has HTTP status 400 and no "quota" substring in its message, so the existing
# quota_exceeded check never matched it — every upload attempt (first try and every automatic
# retry cycle, every few minutes) kept hammering the API with zero backoff) --------------------

UPLOAD_LIMIT_ERROR = (
    b'{"error": {"errors": [{"reason": "uploadLimitExceeded"}], '
    b'"message": "The user has exceeded the number of videos they may upload."}}'
)


def test_upload_limit_exceeded_records_backoff_and_does_not_raise(monkeypatch, tmp_path, fake_tiktok_success, clip_path):
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")

    def boom(*a, **k):
        raise _make_http_error(400, UPLOAD_LIMIT_ERROR)
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

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

    clip_1 = tmp_path / "clip_1.mp4"
    clip_1.write_bytes(b"clip one bytes")
    clip_2 = tmp_path / "clip_2.mp4"
    clip_2.write_bytes(b"clip two bytes, genuinely different content")

    upload_manager.upload_clip_everywhere(clip_1, "Title", "desc", publish=True)
    assert len(calls) == 1  # first attempt: real call, hits the limit, records backoff

    result = upload_manager.upload_clip_everywhere(clip_2, "Title", "desc", publish=True)
    assert len(calls) == 1  # second attempt: skipped entirely, no new API call
    assert result.youtube.attempted is False
    assert result.youtube.success is False


def test_backoff_clears_after_the_window_elapses(monkeypatch, tmp_path, fake_tiktok_success, clip_path):
    backoff_path = tmp_path / "backoff.json"
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", backoff_path)
    stale_seen_at = upload_manager.datetime.now(upload_manager.timezone.utc) - upload_manager.timedelta(
        minutes=upload_manager.UPLOAD_LIMIT_BACKOFF_MINUTES + 1
    )
    backoff_path.write_text(f'{{"seen_at": "{stale_seen_at.isoformat()}"}}', encoding="utf-8")

    assert upload_manager._upload_limit_backoff_until() is None

    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-video-id")
    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.youtube.attempted is True
    assert result.youtube.success is True


# --- daily "Video Uploads" Data API quota backoff (2026-08-21, found live: this is a THIRD,
# distinct error shape from both cases above -- HTTP 429, reason rateLimitExceeded, message
# naming the 'Video Uploads'/'Video Uploads per day' quota metric. Neither the 400
# uploadLimitExceeded check nor the 403 quota_exceeded check matches it, so it fell into the
# undifferentiated `else` branch with no backoff recorded: every streamer's next cycle AND
# auto_pilot.retry_missing_youtube_uploads() (runs every ~3-7 minutes per streamer) kept
# resubmitting straight into the same 429 across the whole fleet.) ------------------------------

DAILY_VIDEO_QUOTA_ERROR = (
    b'{"error": {"message": "Quota exceeded for quota metric \'Video Uploads\' and limit '
    b'\'Video Uploads per day\' of service \'youtube.googleapis.com\' for consumer '
    b'\'project_number:736355812141\'.", "errors": [{"reason": "rateLimitExceeded", '
    b'"domain": "global", "message": "Quota exceeded for quota metric \'Video Uploads\' and '
    b'limit \'Video Uploads per day\' of service \'youtube.googleapis.com\' for consumer '
    b'\'project_number:736355812141\'."}]}}'
)


def test_daily_video_quota_429_records_backoff_and_does_not_raise(monkeypatch, tmp_path, fake_tiktok_success, clip_path):
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")

    def boom(*a, **k):
        raise _make_http_error(429, DAILY_VIDEO_QUOTA_ERROR)
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.youtube.success is False
    assert result.youtube.attempted is True
    assert upload_manager._upload_limit_backoff_until() is not None


def test_second_attempt_during_daily_video_quota_backoff_skips_the_real_api_call(monkeypatch, tmp_path, fake_tiktok_success):
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise _make_http_error(429, DAILY_VIDEO_QUOTA_ERROR)
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    clip_1 = tmp_path / "clip_1.mp4"
    clip_1.write_bytes(b"clip one bytes")
    clip_2 = tmp_path / "clip_2.mp4"
    clip_2.write_bytes(b"clip two bytes, genuinely different content")

    upload_manager.upload_clip_everywhere(clip_1, "Title", "desc", publish=True)
    assert len(calls) == 1  # first attempt: real call, hits the 429, records backoff

    result = upload_manager.upload_clip_everywhere(clip_2, "Title", "desc", publish=True)
    assert len(calls) == 1  # second attempt: skipped entirely, no new API call
    assert result.youtube.attempted is False
    assert result.youtube.success is False


def test_quota_exceeded_and_upload_limit_exceeded_are_detected_distinctly(monkeypatch, tmp_path, fake_tiktok_success, clip_path):
    # A 403 quotaExceeded must NOT be mistaken for the 400 uploadLimitExceeded case -- they
    # need different handling (quota resets are a Google Cloud project concern, not this
    # channel's own daily cap) and must not share the same backoff state.
    monkeypatch.setattr(upload_manager, "UPLOAD_LIMIT_BACKOFF_PATH", tmp_path / "backoff.json")

    def boom(*a, **k):
        raise _make_http_error(403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')
    monkeypatch.setattr(youtube_uploader, "upload_clip", boom)

    upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert upload_manager._upload_limit_backoff_until() is None


# --- MultiPlatformOutcome.any_success ----------------------------------------------------------

def test_any_success_true_when_only_tiktok_succeeds(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)
    assert result.any_success is True


def test_any_success_true_when_only_youtube_succeeds(monkeypatch, fake_tiktok_failure, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)
    assert result.any_success is True


def test_any_success_false_when_both_fail(monkeypatch, fake_tiktok_failure, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)
    assert result.any_success is False


def test_any_success_false_when_tiktok_succeeds_but_unconfirmed(monkeypatch, clip_path):
    # success=True but confirmed=False must NOT count as a real success (matches how
    # auto_pilot.py itself treats an unconfirmed click as a failure).
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: tiktok_uploader.UploadOutcome(success=True, confirmed=False),
    )
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.any_success is False


# --- add_background_sound passthrough (2026-08-19 feature, must survive this refactor) --------

def test_add_background_sound_flag_passed_through_to_tiktok(monkeypatch, fake_tiktok_success, clip_path):
    calls = []
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: calls.append(k) or fake_tiktok_success,
    )
    upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=False, add_background_sound=False,
    )
    assert calls[0]["add_background_sound"] is False


# --- Instagram gating (2026-08-21) — a separate opt-in from publish itself, since
# upload_instagram_playwright.py's automation has never been verified against a live session
# (see that module's own docstring): instagram_enabled must default False and require BOTH
# publish=True and instagram_enabled=True before the real automation is ever touched --------

def test_instagram_not_attempted_when_disabled_even_with_publish_true(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    called = []
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", lambda *a, **k: called.append(1))

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)  # instagram_enabled defaults False

    assert called == []
    assert result.instagram.attempted is False
    assert result.instagram.success is False


def test_instagram_not_attempted_when_publish_false_even_if_enabled(monkeypatch, fake_tiktok_success, clip_path):
    called = []
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", lambda *a, **k: called.append(1))

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=False, instagram_enabled=True,
    )

    assert called == []
    assert result.instagram.attempted is False


def test_instagram_attempted_when_both_publish_and_enabled_true(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=True),
    )

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.instagram.attempted is True
    assert result.instagram.success is True
    assert result.instagram.confirmed is True


def test_instagram_exception_does_not_raise_or_block_other_platforms(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")

    def boom(*a, **k):
        raise RuntimeError("playwright blew up")
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.instagram.success is False
    assert "playwright blew up" in result.instagram.detail
    assert result.tiktok.success is True
    assert result.youtube.success is True


# --- In-call retry with exponential backoff (2026-08-22 upload-parity audit) ---------------

def test_instagram_retries_a_failure_and_succeeds_on_a_later_attempt(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) < upload_manager.INSTAGRAM_UPLOAD_MAX_RETRIES:
            return instagram_uploader.UploadOutcome(success=False, detail="transient")
        return instagram_uploader.UploadOutcome(success=True, confirmed=True)
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", flaky)

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert len(calls) == upload_manager.INSTAGRAM_UPLOAD_MAX_RETRIES
    assert result.instagram.success is True
    assert result.instagram.confirmed is True


def test_instagram_gives_up_after_max_retries_and_marks_failed(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    calls = []
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: (calls.append(1), instagram_uploader.UploadOutcome(success=False, detail="stuck"))[1],
    )

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert len(calls) == upload_manager.INSTAGRAM_UPLOAD_MAX_RETRIES
    assert result.instagram.success is False
    content_hash = upload_ledger.compute_content_hash(clip_path)
    assert upload_ledger.get_entry(content_hash, "instagram")["status"] == "failed"


def test_instagram_does_not_retry_an_unconfirmed_success(monkeypatch, fake_tiktok_success, clip_path):
    # A "clicked but unconfirmed" outcome means the Share button was actually clicked --
    # retrying it risks a real duplicate Reel post, so this must stop after one attempt.
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    calls = []
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: (calls.append(1), instagram_uploader.UploadOutcome(success=True, confirmed=False))[1],
    )

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert len(calls) == 1
    assert result.instagram.success is True
    assert result.instagram.confirmed is False


def test_instagram_succeeds_on_first_attempt_without_extra_retries(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    calls = []
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: (calls.append(1), instagram_uploader.UploadOutcome(success=True, confirmed=True))[1],
    )

    upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True, instagram_enabled=True)

    assert len(calls) == 1


def test_instagram_retries_across_repeated_exceptions_then_gives_up(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("playwright blew up")
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", boom)

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert len(calls) == upload_manager.INSTAGRAM_UPLOAD_MAX_RETRIES
    assert result.instagram.success is False
    assert "playwright blew up" in result.instagram.detail


def test_any_success_true_when_only_instagram_succeeds(monkeypatch, fake_tiktok_failure, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=True),
    )

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.any_success is True


def test_any_success_false_when_instagram_succeeds_but_unconfirmed(monkeypatch, fake_tiktok_failure, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=False),
    )

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert result.any_success is False


# --- Duplicate-upload protection (2026-08-21) — see upload_ledger.py's own module docstring
# for the two real production incidents this closes: a single real clip getting 3 separate
# real YouTube videos and 3 separate real Instagram posts because nothing durable recorded
# what had already succeeded across retries, crashes, and supervisor restarts. -----------------

def test_youtube_not_reuploaded_when_ledger_already_shows_done(monkeypatch, fake_tiktok_success, clip_path):
    calls = []
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: calls.append(1) or "should-not-be-called")

    content_hash = upload_ledger.compute_content_hash(clip_path)
    upload_ledger.mark_done(content_hash, "youtube", video_id="already-uploaded-id")

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert calls == []
    assert result.youtube.success is True
    assert result.youtube.video_id == "already-uploaded-id"


def test_instagram_not_reuploaded_when_ledger_already_shows_done(monkeypatch, fake_tiktok_success, clip_path):
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    calls = []
    monkeypatch.setattr(instagram_uploader, "try_upload_clip", lambda *a, **k: calls.append(1))

    content_hash = upload_ledger.compute_content_hash(clip_path)
    upload_ledger.mark_done(content_hash, "instagram")

    result = upload_manager.upload_clip_everywhere(
        clip_path, "Title", "desc", publish=True, instagram_enabled=True,
    )

    assert calls == []
    assert result.instagram.success is True
    assert result.instagram.confirmed is True


def test_tiktok_not_reuploaded_when_ledger_already_shows_done(monkeypatch, clip_path):
    calls = []
    monkeypatch.setattr(
        tiktok_uploader, "try_upload_clip",
        lambda *a, **k: calls.append(1) or tiktok_uploader.UploadOutcome(success=True, confirmed=True),
    )
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")

    content_hash = upload_ledger.compute_content_hash(clip_path)
    upload_ledger.mark_done(content_hash, "tiktok")

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert calls == []
    assert result.tiktok.success is True
    assert result.tiktok.confirmed is True


def test_fresh_pending_entry_blocks_a_concurrent_duplicate_attempt(monkeypatch, fake_tiktok_success, clip_path):
    """Simulates a second worker (or a retry that races an in-flight first attempt) reaching
    _upload_to_youtube() for the exact same clip while the first attempt's pending marker is
    still fresh — must be blocked, not allowed to fire a second real upload concurrently."""
    calls = []
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: calls.append(1) or "yt-id")

    content_hash = upload_ledger.compute_content_hash(clip_path)
    upload_ledger.try_mark_pending(content_hash, "youtube", title="Title")  # simulates the "other worker"

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert calls == []
    assert result.youtube.attempted is False
    assert result.youtube.success is False


def test_stale_pending_entry_is_released_for_a_fresh_attempt(monkeypatch, fake_tiktok_success, clip_path):
    """A pending marker older than PENDING_STALE_MINUTES means the process that created it
    (crashed worker, killed supervisor) never resolved it — must not permanently block this
    clip from ever being retried."""
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")

    content_hash = upload_ledger.compute_content_hash(clip_path)
    stale_time = upload_ledger.datetime.now(upload_ledger.timezone.utc) - upload_ledger.timedelta(
        minutes=upload_ledger.PENDING_STALE_MINUTES + 1
    )
    upload_ledger._save_ledger({content_hash: {"youtube": {"status": "pending", "updated_at": stale_time.isoformat()}}})

    result = upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True)

    assert result.youtube.attempted is True
    assert result.youtube.success is True


def test_instagram_unconfirmed_success_marks_unresolved_not_done_or_failed(monkeypatch, fake_tiktok_success, clip_path):
    """success=True but confirmed=False (Instagram clicked but no confirming signal observed)
    must not be recorded as "done" (unconfirmed is not proof) or "failed" (an immediate retry
    could double-post something that may have actually succeeded) — it stays "pending",
    subject to the same cooldown as a genuine in-flight attempt."""
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: "yt-id")
    monkeypatch.setattr(
        instagram_uploader, "try_upload_clip",
        lambda *a, **k: instagram_uploader.UploadOutcome(success=True, confirmed=False),
    )

    upload_manager.upload_clip_everywhere(clip_path, "Title", "desc", publish=True, instagram_enabled=True)

    content_hash = upload_ledger.compute_content_hash(clip_path)
    entry = upload_ledger.get_entry(content_hash, "instagram")
    assert entry["status"] == "pending"
    assert upload_ledger.is_done(content_hash, "instagram") is False


def test_two_different_clips_with_same_title_are_not_confused_by_the_ledger(monkeypatch, fake_tiktok_success, tmp_path):
    """The ledger is keyed by content hash, not by title — two genuinely different clips that
    happen to share an AI-generated title (seen live: "Nico testet Calisthenics" recurred
    across separate stream sessions) must not be treated as the same upload."""
    # Pacing is orthogonal to what this test checks (ledger dedup by content hash, not by
    # title) — without disabling it, clip_b's real-time-adjacent attempt would be legitimately
    # denied by the fleet-wide pacing interval instead of by anything ledger-related, which
    # would make this test about pacing timing instead of the dedup behavior it's named for.
    monkeypatch.setattr(upload_manager, "TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(upload_manager, "YOUTUBE_MIN_UPLOAD_INTERVAL_SECONDS", 0)
    calls = []
    monkeypatch.setattr(youtube_uploader, "upload_clip", lambda *a, **k: calls.append(1) or f"yt-id-{len(calls)}")

    clip_a = tmp_path / "clip_a.mp4"
    clip_a.write_bytes(b"first clip's real content")
    clip_b = tmp_path / "clip_b.mp4"
    clip_b.write_bytes(b"second clip's completely different real content")

    result_a = upload_manager.upload_clip_everywhere(clip_a, "Same Title", "desc", publish=True)
    result_b = upload_manager.upload_clip_everywhere(clip_b, "Same Title", "desc", publish=True)

    assert len(calls) == 2  # both genuinely uploaded, not deduplicated against each other
    assert result_a.youtube.video_id != result_b.youtube.video_id
