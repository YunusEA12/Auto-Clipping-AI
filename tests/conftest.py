"""Global test safety net: no test may ever perform a real network/OAuth call to Google's
YouTube API, or a real multi-second sleep, no matter which module ends up calling into them.

Found live, 2026-08-20: two existing tests in test_auto_pilot_deployment_gating.py called
run_deployment_phase(publish=True) without knowing that function had grown a new YouTube
upload path (upload_manager.py). With a real client_secret.json now in place and no
token.json yet, this triggered a genuine OAuth flow and uploaded a real, PUBLIC, garbage
video (literally the bytes b"fake video bytes") to the account owner's actual YouTube
channel — and, separately, each test blocked for a real 30-60s sleep (upload_manager's
pacing delay between platforms) since that wasn't mocked either.

These two autouse fixtures make both mistakes structurally impossible project-wide: real
calls raise loudly (an immediate, obvious test failure) instead of silently doing something
real. A test that genuinely needs to exercise the real function bodies overrides these
explicitly and locally (see tests/test_upload.py's own fake_youtube fixture, and
tests/test_upload_manager.py's _no_real_sleep fixture) — the local override always wins,
since it's applied later in the same test's setup."""

import pytest

import upload


class RealYouTubeCallBlocked(RuntimeError):
    """Raised instead of ever performing a real Google OAuth/API call during tests."""


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
