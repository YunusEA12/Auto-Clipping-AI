from pathlib import Path
from types import SimpleNamespace

from autoclip.core import notify


def test_missing_webhook_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    assert notify.send_notification("Clip", 8, Path("clip.mp4"), "rendered") is False


def test_notification_contains_clip_information_and_bounded_energy(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/fixture-webhook")
    calls = []
    monkeypatch.setattr(
        notify.requests, "post", lambda *a, **k: calls.append(k) or SimpleNamespace(raise_for_status=lambda: None)
    )
    assert notify.send_notification("Clip", 99, Path("clip.mp4"), "uploaded", "A moment", ["gaming"])
    message = calls[0]["json"]["content"]
    assert "(10/10)" in message and "#gaming" in message and "clip.mp4" in message


def test_webhook_failure_never_crashes_pipeline(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.invalid/fixture-webhook")

    def fail(*a, **k):
        raise notify.requests.RequestException("fixture network failure")

    monkeypatch.setattr(notify.requests, "post", fail)
    assert notify.send_notification("Clip", 5, Path("clip.mp4"), "failed") is False
