from datetime import datetime, timedelta, timezone

import optimization_engine


# --- classify_title_style ---------------------------------------------------------------

def test_classify_title_style_question():
    assert optimization_engine.classify_title_style("Wait, did that really just happen?") == "question"


def test_classify_title_style_list_number():
    assert optimization_engine.classify_title_style("3 Words That Changed Everything") == "list_number"


def test_classify_title_style_exclamation():
    assert optimization_engine.classify_title_style("This Chat Message Ended The Stream!") == "exclamation"


def test_classify_title_style_statement_fallback():
    assert optimization_engine.classify_title_style("I Broke My PC Live On Stream") == "statement"


def test_classify_title_style_empty_title():
    assert optimization_engine.classify_title_style("") == "unknown"
    assert optimization_engine.classify_title_style(None) == "unknown"


# --- analyze_performance -----------------------------------------------------------------

def _entry(views, likes, checked_at="2026-08-21T00:00:00+00:00", **attrs):
    return {
        "tiktok_views": views, "tiktok_likes": likes,
        "youtube_views": None, "youtube_likes": None,
        "checked_at": checked_at,
        **attrs,
    }


def test_analyze_performance_excludes_unmeasured_entries():
    # No checked_at (never fetched) and zero views (fetched, but nothing to measure yet) —
    # neither should count toward sample_size_total.
    memory = {
        "a": {"tiktok_views": None, "tiktok_likes": None, "checked_at": None, "layout": "full_cam"},
        "b": _entry(0, 0, layout="full_cam"),
    }
    result = optimization_engine.analyze_performance(memory)
    assert result["sample_size_total"] == 0


def test_analyze_performance_requires_min_samples_before_preferring_a_bucket():
    # Only 2 measured uploads for "full_cam" — below MIN_SAMPLES_PER_BUCKET (5) — so layout
    # must show up as insufficient data, not a confident preference.
    memory = {
        f"clip_{i}": _entry(100, 5, layout="full_cam")
        for i in range(2)
    }
    result = optimization_engine.analyze_performance(memory)
    assert "layout" in result["insufficient_data_for"]
    assert "layout" not in result["preferred"]


def test_analyze_performance_prefers_the_higher_engagement_bucket_once_eligible():
    memory = {}
    # full_cam: 6 samples, high engagement (20 likes / 100 views = 20%)
    for i in range(6):
        memory[f"full_cam_{i}"] = _entry(100, 20, layout="full_cam")
    # split_screen: 6 samples, low engagement (2 likes / 100 views = 2%)
    for i in range(6):
        memory[f"split_{i}"] = _entry(100, 2, layout="split_screen")

    result = optimization_engine.analyze_performance(memory)
    assert result["preferred"]["layout"] == "full_cam"
    assert result["attributes"]["layout"]["full_cam"]["n"] == 6
    assert result["attributes"]["layout"]["full_cam"]["avg_engagement_rate"] == 0.2
    assert any("layout=full_cam" in line for line in result["summary_lines"])


def test_analyze_performance_ignores_entries_missing_the_attribute():
    # Older clips predating this feature have no "layout" field at all — must not be
    # silently bucketed under some default value.
    memory = {
        f"clip_{i}": _entry(100, 10)  # no layout key
        for i in range(6)
    }
    result = optimization_engine.analyze_performance(memory)
    assert result["attributes"]["layout"] == {}
    assert "layout" in result["insufficient_data_for"]


# --- build_prompt_section -----------------------------------------------------------------

def test_build_prompt_section_empty_when_no_summary_lines():
    assert optimization_engine.build_prompt_section({"summary_lines": []}) == ""
    assert optimization_engine.build_prompt_section({}) == ""


def test_build_prompt_section_includes_summary_and_soft_language():
    state = {
        "sample_size_total": 12,
        "llm_summary_lines": ["hook_style=curiosity_gap (n=6, 20.0% engagement vs 11.0% avg across measured hook_style values)"],
    }
    section = optimization_engine.build_prompt_section(state)
    assert "hook_style=curiosity_gap" in section
    assert "SOFT" in section
    assert "retention/watch-time/completion-rate are not tracked" in section


def test_build_prompt_section_excludes_non_llm_actionable_attributes():
    # layout/music_track are decided entirely by process.py's own code, not the LLM's output
    # — analyze_performance() must not put them in llm_summary_lines at all, and even if a
    # caller mistakenly passed one via summary_lines, build_prompt_section() must not surface
    # it (found in review, 2026-08-21).
    state = {
        "sample_size_total": 12,
        "summary_lines": ["layout=full_cam (n=6, 20.0% engagement vs 11.0% avg across measured layout values)"],
        "llm_summary_lines": [],
    }
    assert optimization_engine.build_prompt_section(state) == ""


def test_analyze_performance_only_puts_llm_actionable_attributes_in_llm_summary_lines():
    memory = {}
    for i in range(6):
        memory[f"layout_{i}"] = _entry(100, 20, layout="full_cam")
    for i in range(6):
        memory[f"hook_{i}"] = _entry(100, 20, hook_style="curiosity_gap")

    result = optimization_engine.analyze_performance(memory)
    assert any("layout=full_cam" in line for line in result["summary_lines"])
    assert not any("layout=" in line for line in result["llm_summary_lines"])
    assert any("hook_style=curiosity_gap" in line for line in result["llm_summary_lines"])


# --- preferred_music_track ----------------------------------------------------------------

def test_preferred_music_track_reads_from_state(monkeypatch, tmp_path):
    state_path = tmp_path / "optimization_state.json"
    state_path.write_text('{"preferred": {"music_track": "lofi_drift_g_minor.mp3"}}', encoding="utf-8")
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", state_path)
    assert optimization_engine.preferred_music_track() == "lofi_drift_g_minor.mp3"


def test_preferred_music_track_none_when_state_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", tmp_path / "nope.json")
    assert optimization_engine.preferred_music_track() is None


# --- run_daily_report gating --------------------------------------------------------------

def test_run_daily_report_runs_when_no_prior_state(monkeypatch, tmp_path):
    monkeypatch.setattr(optimization_engine, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", tmp_path / "optimization_state.json")

    result = optimization_engine.run_daily_report()
    assert result is not None
    assert "generated_at" in result
    assert (tmp_path / "optimization_state.json").exists()


def test_run_daily_report_skips_when_too_soon(monkeypatch, tmp_path):
    state_path = tmp_path / "optimization_state.json"
    monkeypatch.setattr(optimization_engine, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", state_path)

    first = optimization_engine.run_daily_report()
    assert first is not None

    second = optimization_engine.run_daily_report()
    assert second is None  # ran seconds ago — must not re-run


def test_run_daily_report_force_always_reruns(monkeypatch, tmp_path):
    state_path = tmp_path / "optimization_state.json"
    monkeypatch.setattr(optimization_engine, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", state_path)

    optimization_engine.run_daily_report()
    forced = optimization_engine.run_daily_report(force=True)
    assert forced is not None


def test_run_daily_report_reruns_after_interval_elapses(monkeypatch, tmp_path):
    state_path = tmp_path / "optimization_state.json"
    monkeypatch.setattr(optimization_engine, "VIRAL_MEMORY_PATH", tmp_path / "viral_memory.json")
    monkeypatch.setattr(optimization_engine, "OPTIMIZATION_STATE_PATH", state_path)

    stale_timestamp = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    state_path.write_text(f'{{"last_report_at": "{stale_timestamp}"}}', encoding="utf-8")

    result = optimization_engine.run_daily_report()
    assert result is not None
