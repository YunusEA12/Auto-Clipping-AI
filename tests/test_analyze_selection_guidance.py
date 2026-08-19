"""2026-08-19: manually-uploaded test clips got flagged by TikTok's duplicate/unoriginal-
content detection — hundreds of other channels clip the same streamers, and the most
obvious spectacle moment (the loudest scream, the one huge kill) is exactly what everyone
else cuts too, at nearly the same timestamp. The fix is content-selection guidance steering
the LLM toward "hidden gem" moments (dry/sarcastic humor, chat interactions, quiet
tension-building beats) over the obvious highlight-reel pick — not pixel/hash randomization
to evade the platform's detection, which was considered and explicitly declined as
adversarial evasion of anti-spam moderation, not a video-quality feature.

A follow-up request then sharpened this from a mere tie-breaker ("prefer X when comparably
strong") to a genuine shift in what counts as a strong pick ("prefer X over a bigger
spectacle moment when X has the stronger hook, not only when tied")."""

import analyze


def test_prompt_frames_obvious_spectacle_as_a_fallback_not_the_default():
    assert "Treat\n  the obvious spectacle pick as a fallback, not the default." in analyze.BASE_SYSTEM_PROMPT


def test_prompt_names_all_three_hidden_gem_categories():
    assert "sarcastic humor" in analyze.BASE_SYSTEM_PROMPT
    assert "real talk" in analyze.BASE_SYSTEM_PROMPT
    assert "builds tension" in analyze.BASE_SYSTEM_PROMPT


def test_prompt_still_requires_a_real_payoff_for_quiet_moments():
    # A quiet/awkward pick must still resolve into something — not a license for dead air.
    assert "quality gate above still fully applies" in analyze.BASE_SYSTEM_PROMPT


def test_prompt_is_a_genuine_shift_not_just_a_tie_breaker():
    assert "not a tie-breaker" in analyze.BASE_SYSTEM_PROMPT


def test_prompt_still_guards_against_weak_material():
    assert "license to force a clip out of genuinely weak material" in analyze.BASE_SYSTEM_PROMPT
