"""2026-08-19: three related prompt-contract fixes.
1. Clip duration bounds tightened to 30-90s (previously 8-120s).
2. Hashtags: the LLM occasionally emits a malformed entry like "#" or "# " despite being
   told 3-5 real, valid tags — Clip.hashtags now has an actual Pydantic validator as a
   backstop, not just a prompt instruction.
3. Every caption must credit the source streamer with "@<streamer_name>" once known."""

import analyze


def _clip(**overrides):
    base = dict(
        start_time=0.0, end_time=40.0, title="Titel", hook_explanation="Weil es lustig ist",
        viral_score=7, energy_rating=6, description="Krasser Moment!",
        hashtags=["#gaming", "#viral"],
    )
    base.update(overrides)
    return analyze.Clip(**base)


# --- clip duration bounds -------------------------------------------------------------

def test_clip_duration_bounds_are_30_to_90_seconds():
    assert analyze.MIN_CLIP_DURATION == 30
    assert analyze.MAX_CLIP_DURATION == 90


# --- hashtag validator -----------------------------------------------------------------

def test_hashtag_validator_strips_empty_and_whitespace_only_tags():
    clip = _clip(hashtags=["#gaming", "#", "# ", "", "   ", "#viral"])
    assert clip.hashtags == ["#gaming", "#viral"]


def test_hashtag_validator_adds_missing_hash_prefix():
    clip = _clip(hashtags=["gaming", "#viral"])
    assert clip.hashtags == ["#gaming", "#viral"]


def test_hashtag_validator_caps_at_max_hashtags():
    clip = _clip(hashtags=[f"#tag{i}" for i in range(10)])
    assert len(clip.hashtags) == analyze.MAX_HASHTAGS


def test_hashtag_validator_keeps_well_formed_tags_untouched():
    clip = _clip(hashtags=["#fyp", "#viral", "#gaming"])
    assert clip.hashtags == ["#fyp", "#viral", "#gaming"]


# --- streamer @-mention section ---------------------------------------------------------

def test_build_streamer_mention_section_empty_without_name():
    assert analyze.build_streamer_mention_section(None) == ""
    assert analyze.build_streamer_mention_section("") == ""


def test_build_streamer_mention_section_includes_at_mention():
    section = analyze.build_streamer_mention_section("papaplatte")
    assert "@papaplatte" in section


def test_build_system_prompt_threads_streamer_name_through():
    prompt_with = analyze.build_system_prompt(profile=None, streamer_name="papaplatte")
    prompt_without = analyze.build_system_prompt(profile=None, streamer_name=None)
    assert "@papaplatte" in prompt_with
    assert "@papaplatte" not in prompt_without


# --- fallback clip (find_longest_segment_fallback) --------------------------------------

def _transcript_with_one_long_segment(duration=40.0):
    return {"segments": [{"start": 0.0, "end": duration, "text": "Ein langer, zusammenhängender Moment."}]}


def test_fallback_clip_includes_mention_when_streamer_name_given():
    selection = analyze.find_longest_segment_fallback(_transcript_with_one_long_segment(), streamer_name="papaplatte")
    assert "@papaplatte" in selection.clips[0].description


def test_fallback_clip_omits_mention_without_streamer_name():
    selection = analyze.find_longest_segment_fallback(_transcript_with_one_long_segment())
    assert "@" not in selection.clips[0].description


def test_fallback_clip_hashtag_count_within_new_bounds():
    selection = analyze.find_longest_segment_fallback(_transcript_with_one_long_segment())
    assert 3 <= len(selection.clips[0].hashtags) <= analyze.MAX_HASHTAGS
