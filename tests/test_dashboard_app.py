from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from autoclip.dashboard import dashboard_api


def test_dashboard_boots_without_rendering_hidden_archive(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setattr(
        dashboard_api,
        "archive_page",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("hidden archive must not load")),
    )
    app = Path(__file__).parents[1] / "app.py"
    result = AppTest.from_file(str(app), default_timeout=20).run()
    assert not result.exception
    assert len(result.tabs) == 7


def test_dashboard_password_gate_precedes_backend_state(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "fixture-dashboard-password")
    monkeypatch.setattr(
        dashboard_api,
        "list_agent_state_paths",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("not authenticated")),
    )
    result = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert not result.exception
    assert len(result.text_input) == 1
    assert "Passwort" in result.text_input[0].label


@pytest.mark.parametrize("tab_index", range(7))
def test_each_dashboard_section_opens_without_loading_another_section(monkeypatch, tab_index):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    result = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    label = result.tabs[tab_index].label
    result.session_state["main_navigation"] = label
    result.run()
    assert not result.exception
    assert len(result.subheader) >= 1
