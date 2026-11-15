"""Keep pipeline placeholder videos out of a running quality-audit campaign."""

import sys
from pathlib import Path

import pytest

from autoclip import runtime
from autoclip.quality import clip_quality_audit


@pytest.fixture(autouse=True)
def _isolated_quality_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(clip_quality_audit, "AUDIT_ROOT", tmp_path / "quality_audit")


@pytest.fixture(autouse=True)
def _isolated_application_runtime(tmp_path_factory, monkeypatch):
    """Tests and child CLIs must never write to a developer's live runtime."""
    production_home = runtime.HOME
    test_home = tmp_path_factory.mktemp("application")
    runtime.ensure_directories(home=test_home)
    monkeypatch.setenv("AUTOCLIP_HOME", str(test_home))
    monkeypatch.setattr(runtime, "HOME", test_home)
    for module in list(sys.modules.values()):
        if not getattr(module, "__name__", "").startswith("autoclip."):
            continue
        for name, value in list(vars(module).items()):
            if isinstance(value, Path) and any(
                value.is_relative_to(production_home / part) for part in ("var", "config")
            ):
                monkeypatch.setattr(module, name, test_home / value.relative_to(production_home))
