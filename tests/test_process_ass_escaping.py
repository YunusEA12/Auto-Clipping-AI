import process


# --- _escape_ass_text (found in review, 2026-08-18: a literal '{' or '}' in transcribed
# speech would be parsed by libass as an override-tag delimiter, corrupting or swallowing
# that or a subsequent subtitle line when burned into the final render) --------------------

def test_escape_ass_text_strips_braces():
    assert process._escape_ass_text("hello {world}") == "hello world"


def test_escape_ass_text_leaves_normal_text_untouched():
    assert process._escape_ass_text("Krasser Moment!") == "Krasser Moment!"


def test_fallback_segment_event_strips_brace_from_transcript_text():
    seg = {"start": 0.0, "end": 2.0, "text": "he said {this} loudly"}
    event = process._fallback_segment_event(seg, clip_start=0.0, clip_end=2.0, position_tag="{\\an2}")
    assert event is not None
    # The deliberately-inserted position_tag's braces must survive; only the
    # transcript-derived text's braces are stripped.
    assert event.startswith("Dialogue: 0,")
    assert "{\\an2}" in event
    assert "{this}" not in event
    assert "THIS" in event


def test_word_block_events_strip_brace_from_word_text_without_touching_highlight_tags():
    words = [
        {"text": "say", "start": 0.0, "end": 0.5},
        {"text": "{glitch}", "start": 0.5, "end": 1.0},
    ]
    events = process._word_block_events(
        words, clip_start=0.0, clip_end=1.0, position_tag="{\\an2}", highlight_color="FFFFFF",
    )
    assert events
    joined = " ".join(events)
    assert "{glitch}" not in joined
    assert "GLITCH" in joined
    # The real karaoke-highlight override tags must still be present.
    assert "{\\c&HFFFFFF&}" in joined
