import sys
from types import SimpleNamespace

import pytest

from autoclip import __main__ as cli


def test_cli_forwards_command_flags_without_enabling_publish(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "import_module", lambda name: calls.append(name) or SimpleNamespace(main=lambda: 0))
    monkeypatch.setattr(sys, "argv", ["autoclip", "uploads", "--once"])
    assert cli.main() == 0
    assert calls == ["autoclip.control.upload_worker"]
    assert sys.argv[1:] == ["--once"]
    assert "--publish" not in sys.argv


def test_cli_reports_invalid_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["autoclip", "does-not-exist"])
    with pytest.raises(SystemExit) as failure:
        cli.main()
    assert failure.value.code == 2


def test_cli_passes_through_nonzero_service_exit(monkeypatch):
    monkeypatch.setattr(cli, "import_module", lambda name: SimpleNamespace(main=lambda: 1))
    monkeypatch.setattr(sys, "argv", ["autoclip", "check"])
    assert cli.main() == 1


def test_cli_help_never_imports_pipeline_services(monkeypatch):
    monkeypatch.setattr(cli, "import_module", lambda name: pytest.fail("no service imports"))
    monkeypatch.setattr(sys, "argv", ["autoclip", "--help"])
    with pytest.raises(SystemExit) as success:
        cli.main()
    assert success.value.code == 0


@pytest.mark.parametrize("confirmed,exit_code", [(True, 0), (False, 1)])
def test_tiktok_cli_uses_shared_guards_and_requires_confirmation(monkeypatch, tmp_path, confirmed, exit_code):
    from autoclip.publishing import tiktok_uploader, upload_manager

    calls = []
    video = tmp_path / "clip.mp4"
    monkeypatch.setattr(
        upload_manager,
        "upload_tiktok_clip",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(success=True, confirmed=confirmed),
    )
    monkeypatch.setattr(sys, "argv", ["autoclip", str(video), "--publish", "--headed", "--no-music"])
    with pytest.raises(SystemExit) as result:
        tiktok_uploader.main()
    assert result.value.code == exit_code
    assert calls == [((video, "", None), {"publish": True, "headless": False, "add_background_sound": False})]
