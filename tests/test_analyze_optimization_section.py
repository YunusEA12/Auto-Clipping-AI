import analyze
import optimization_engine


def test_load_optimization_preferences_section_empty_when_no_state(monkeypatch, tmp_path):
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", tmp_path / "nope.json")
    assert analyze.load_optimization_preferences_section() == ""


def test_load_optimization_preferences_section_included_in_system_prompt(monkeypatch, tmp_path):
    state_path = tmp_path / "optimization_state.json"
    state_path.write_text(
        '{"sample_size_total": 11, "llm_summary_lines": '
        '["hook_style=curiosity_gap (n=6, 5.0% engagement vs 2.0% avg across measured hook_style values)"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", state_path)

    section = analyze.load_optimization_preferences_section()
    assert "hook_style=curiosity_gap" in section

    prompt = analyze.build_system_prompt()
    assert "hook_style=curiosity_gap" in prompt
