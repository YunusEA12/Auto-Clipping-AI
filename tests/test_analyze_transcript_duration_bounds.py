import types

import pytest

import analyze
import llm_utils


def _clip(title, start, end):
    return analyze.Clip(
        start_time=start, end_time=end, title=title, hook_explanation="x", hook_style="other",
        viral_score=5, energy_rating=5, description="x", hashtags=["#fyp"],
    )


def _fake_response(clips):
    return types.SimpleNamespace(
        candidates=[types.SimpleNamespace(finish_reason=None)],
        parsed=analyze.ClipSelection(clips=clips),
    )


@pytest.fixture(autouse=True)
def _no_real_genai_client(monkeypatch):
    # select_clips() constructs genai.Client() before ever reaching the (mocked, per-test)
    # call_with_fallback — a real Client() requires a live API key, which these tests have no
    # need for since the actual generate_content call is never made.
    monkeypatch.setattr(analyze.genai, "Client", lambda: types.SimpleNamespace(models=None))


# 2026-08-22: gemini-3.x-flash-lite occasionally returns a start_time/end_time that doesn't
# actually occur anywhere in the transcript it was given (small-model numeric drift) — the
# clip then points past the end of the source chunk's actual video. That desyncs
# resolve_layout()'s frame reads and render_clip()'s -ss/-to trim from the real footage,
# producing either an unplayable 0-packet render or a garbled clip the vision critic reliably
# scores as incoherent and deletes. select_clips() now drops any clip whose timestamps fall
# outside the transcript's own known duration before it ever reaches rendering.

def test_select_clips_drops_a_clip_hallucinated_past_the_transcript_duration(monkeypatch):
    in_bounds = _clip("Real Moment", 10.0, 30.0)
    hallucinated = _clip("Hallucinated Moment", 221.9, 240.6)  # transcript only covers 0-180s

    monkeypatch.setattr(
        llm_utils, "call_with_fallback",
        lambda fn, description, model_pool: _fake_response([in_bounds, hallucinated]),
    )

    selection = analyze.select_clips("Transcript:\n[00:00] hi", transcript_duration=180.0)

    assert [c.title for c in selection.clips] == ["Real Moment"]


def test_select_clips_allows_a_clip_within_a_small_rounding_tolerance(monkeypatch):
    almost_in_bounds = _clip("Almost At The Edge", 160.0, 180.4)  # 0.4s past a 180.0s transcript

    monkeypatch.setattr(
        llm_utils, "call_with_fallback",
        lambda fn, description, model_pool: _fake_response([almost_in_bounds]),
    )

    selection = analyze.select_clips("Transcript:\n[00:00] hi", transcript_duration=180.0)

    assert [c.title for c in selection.clips] == ["Almost At The Edge"]


def test_select_clips_skips_the_bounds_check_when_transcript_duration_is_not_given(monkeypatch):
    far_out = _clip("No Duration Known", 500.0, 520.0)

    monkeypatch.setattr(
        llm_utils, "call_with_fallback",
        lambda fn, description, model_pool: _fake_response([far_out]),
    )

    selection = analyze.select_clips("Transcript:\n[00:00] hi")

    assert [c.title for c in selection.clips] == ["No Duration Known"]
