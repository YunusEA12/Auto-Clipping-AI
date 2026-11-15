import subprocess
import sys
from pathlib import Path

from autoclip import runtime
from autoclip.control import orchestrator


def test_installed_cli_is_available_outside_repository(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "autoclip", "--version"], cwd=tmp_path, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.2.0"


def test_packaged_resources_exist():
    assert (runtime.RESOURCES / "profiles/default_streamer.json").exists()
    assert (runtime.RESOURCES / "background_music/tags.json").exists()


def test_monitoring_api_does_not_import_inference_or_rendering_models(tmp_path):
    code = "from autoclip.dashboard import dashboard_api; import sys; assert 'faster_whisper' not in sys.modules; assert 'cv2' not in sys.modules"
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_new_process_command_uses_installed_module():
    command = orchestrator.build_auto_pilot_cmd({"name": "creator", "url": "https://twitch.tv/creator"})
    assert command[1:3] == ["-m", "autoclip.control.auto_pilot"]


def test_no_backend_modules_remain_at_project_root():
    root = Path(__file__).parents[1]
    assert {p.name for p in root.glob("*.py")} == {"app.py", "conftest.py"}
