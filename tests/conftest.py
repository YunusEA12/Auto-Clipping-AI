"""Global test safety net: no test may ever perform a real network/OAuth call to Google's
YouTube API, a real Playwright-driven Instagram upload, or a real multi-second sleep, no
matter which module ends up calling into them.

Found live, 2026-08-20: two existing tests in test_auto_pilot_deployment_gating.py called
run_deployment_phase(publish=True) without knowing that function had grown a new YouTube
upload path (upload_manager.py). With a real client_secret.json now in place and no
token.json yet, this triggered a genuine OAuth flow and uploaded a real, PUBLIC, garbage
video (literally the bytes b"fake video bytes") to the account owner's actual YouTube
channel — and, separately, each test blocked for a real 30-60s sleep (upload_manager's
pacing delay between platforms) since that wasn't mocked either.

The Instagram block below (2026-08-21) is added proactively, before this same mistake has a
chance to recur — upload_manager.py's Instagram leg is exactly the same shape (an
upload_clip_everywhere(publish=True) call reaching a real browser-automation upload) that
caused the YouTube incident above, and once config/instagram_cookies.json exists for real, an
unmocked test here would attempt a real post to the account owner's actual Instagram account.

These autouse fixtures make each mistake structurally impossible project-wide: real calls
raise loudly (an immediate, obvious test failure) instead of silently doing something real. A
test that genuinely needs to exercise the real function bodies overrides these explicitly and
locally (see tests/test_upload.py's own fake_youtube fixture, and
tests/test_upload_manager.py's _no_real_sleep fixture) — the local override always wins,
since it's applied later in the same test's setup."""

import pytest

import upload
import upload_instagram_playwright


class RealYouTubeCallBlocked(RuntimeError):
    """Raised instead of ever performing a real Google OAuth/API call during tests."""


class RealInstagramCallBlocked(RuntimeError):
    """Raised instead of ever launching a real Playwright browser against Instagram during
    tests."""


class RealSleepBlocked(RuntimeError):
    """Raised instead of ever actually blocking a test on time.sleep() for more than a
    trivial moment — anything a test needs to wait out should be mocked, not endured."""


@pytest.fixture(autouse=True)
def _block_real_youtube_calls(monkeypatch):
    def _raise(*args, **kwargs):
        raise RealYouTubeCallBlocked(
            "A test tried to call upload.get_authenticated_service() for real. Mock it "
            "explicitly (see tests/test_upload.py's fake_youtube fixture) if this test "
            "genuinely needs to exercise that code path."
        )
    monkeypatch.setattr(upload, "get_authenticated_service", _raise)

    def _raise_stats(*args, **kwargs):
        raise RealYouTubeCallBlocked(
            "A test tried to call upload.get_stats_service() for real (added 2026-08-21 for "
            "metrics_tracker.py's YouTube view/like fetch). Mock it explicitly if this test "
            "genuinely needs to exercise that code path."
        )
    monkeypatch.setattr(upload, "get_stats_service", _raise_stats)


@pytest.fixture(autouse=True)
def _block_real_instagram_calls(monkeypatch):
    def _raise(*args, **kwargs):
        raise RealInstagramCallBlocked(
            "A test tried to call upload_instagram_playwright.upload_video() for real. Mock "
            "upload_instagram_playwright.try_upload_clip() (or .upload_video()) explicitly if "
            "this test genuinely needs to exercise that code path — see "
            "tests/test_upload_manager.py's Instagram-gating tests for the pattern."
        )
    monkeypatch.setattr(upload_instagram_playwright, "upload_video", _raise)


@pytest.fixture(autouse=True)
def _block_real_sleep(monkeypatch):
    real_sleep = __import__("time").sleep

    def _guarded_sleep(seconds, *args, **kwargs):
        if seconds > 1:
            raise RealSleepBlocked(
                f"A test tried to time.sleep({seconds!r}) for real. Mock time.sleep on "
                "whichever module actually calls it if this delay is expected."
            )
        real_sleep(seconds)

    monkeypatch.setattr("time.sleep", _guarded_sleep)
