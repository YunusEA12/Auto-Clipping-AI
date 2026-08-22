import train_loop


def _transcript(*segs):
    return {"segments": [{"start": s, "end": e, "text": t} for s, e, t in segs]}


# --- extract_clip_context_text (2026-08-22: short reaction clips were reading as "no
# context" to the critic because it only ever saw the clip's own isolated transcript slice) --

def test_context_text_pulls_lead_in_before_clip_start():
    transcript = _transcript(
        (0.0, 5.0, "Weit weg, außerhalb des Lookback-Fensters."),
        (12.0, 16.0, "Er öffnet die Kiste."),
        (16.0, 20.0, "Das ist ein legendäres Item!"),
        (30.0, 34.0, "WAS?! NEIN!"),
    )
    context = train_loop.extract_clip_context_text(transcript, start=30.0)
    assert "legendäres Item" in context
    assert "Weit weg" not in context  # outside the lookback window
    assert "WAS?! NEIN!" not in context  # the clip's own text is never included as context


def test_context_text_respects_lookback_window():
    transcript = _transcript(
        (0.0, 5.0, "Weit weg, sollte nicht auftauchen."),
        (25.0, 29.0, "Direkt davor."),
    )
    context = train_loop.extract_clip_context_text(transcript, start=30.0, lookback=10.0)
    assert context == "Direkt davor."


def test_context_text_empty_without_transcript():
    assert train_loop.extract_clip_context_text(None, start=30.0) == ""


def test_context_text_empty_when_clip_opens_the_transcript():
    transcript = _transcript((0.0, 5.0, "Direkt am Anfang."))
    assert train_loop.extract_clip_context_text(transcript, start=0.0) == ""


# --- build_critic_user_content wires the lead-in into each clip's block -------------------

def test_critic_content_includes_context_block_when_lead_in_exists():
    transcript = _transcript(
        (10.0, 14.0, "Vorher passiert etwas Wichtiges."),
        (20.0, 25.0, "Die Pointe selbst."),
    )
    clips = [{"title": "Reaction Clip", "start_time": 20.0, "end_time": 25.0, "hook_explanation": "x"}]
    content = train_loop.build_critic_user_content(clips, transcript, {}, {})
    block = content[1]
    assert "Kontext DAVOR" in block
    assert "Vorher passiert etwas Wichtiges" in block
    assert "Die Pointe selbst" in block


def test_critic_content_omits_context_block_when_clip_opens_the_transcript():
    transcript = _transcript((0.0, 5.0, "Die Pointe selbst."))
    clips = [{"title": "Opening Clip", "start_time": 0.0, "end_time": 5.0, "hook_explanation": "x"}]
    content = train_loop.build_critic_user_content(clips, transcript, {}, {})
    block = content[1]
    assert "Kontext DAVOR" not in block


# --- system prompt now explicitly protects short reaction/punchline clips -----------------

def test_prompt_tells_critic_not_to_penalize_short_reaction_clips():
    assert "NOT penalize" in train_loop.CRITIC_SYSTEM_PROMPT or "not itself judged" in train_loop.CRITIC_SYSTEM_PROMPT
    assert "punchline" in train_loop.CRITIC_SYSTEM_PROMPT.lower()


def test_prompt_explains_the_lead_in_context_is_background_only():
    assert "Kontext DAVOR" in train_loop.CRITIC_SYSTEM_PROMPT
