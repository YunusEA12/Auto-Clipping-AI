from types import SimpleNamespace

from playwright import sync_api

from autoclip import runtime
from autoclip.observability import health_check


def fake_browser_inventory(tmp_path, monkeypatch):
    browser = tmp_path / "chromium"
    browser.touch()

    class Inventory:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(executable_path=str(browser)))

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(sync_api, "sync_playwright", Inventory)
    monkeypatch.setattr(health_check, "ROOT", runtime.HOME)


def test_preflight_checks_real_decoder_without_launching_browser(tmp_path, monkeypatch, capsys):
    fake_browser_inventory(tmp_path, monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-configured-key")
    assert health_check.main() == 0
    assert "real WAV decode/resampling" in capsys.readouterr().out


def test_preflight_fails_on_missing_api_configuration(tmp_path, monkeypatch, capsys):
    fake_browser_inventory(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert health_check.main() == 1
    assert "FAIL GEMINI_API_KEY" in capsys.readouterr().out


def test_preflight_blocks_broken_audio_decoder(tmp_path, monkeypatch, capsys):
    from faster_whisper import audio

    fake_browser_inventory(tmp_path, monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-configured-key")

    def fail(*args, **kwargs):
        raise ValueError("incompatible decoder")

    monkeypatch.setattr(audio, "decode_audio", fail)
    assert health_check.main() == 1
    assert "FAIL Whisper audio decoder" in capsys.readouterr().out
