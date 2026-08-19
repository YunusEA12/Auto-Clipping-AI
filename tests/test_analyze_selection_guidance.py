"""2026-08-19: manually-uploaded test clips got flagged by TikTok's duplicate/unoriginal-
content detection — hundreds of other channels clip the same streamers, and the most
obvious spectacle moment (the loudest scream, the one huge kill) is exactly what everyone
else cuts too, at nearly the same timestamp. The fix is content-selection guidance steering
the LLM toward more idiosyncratic, personality-driven moments when candidates are comparably
strong — not pixel/hash randomization to evade the platform's detection, which was
considered and explicitly declined as adversarial evasion of anti-spam moderation, not a
video-quality feature."""

import analyze


def test_prompt_warns_against_the_everyone_clips_this_trap():
    assert "everyone clips this" in analyze.BASE_SYSTEM_PROMPT


def test_prompt_still_allows_genuinely_strong_spectacle_moments():
    # The guidance is a tie-breaker for comparably-strong candidates, not a ban on big plays.
    assert "does not mean avoiding strong spectacle" in analyze.BASE_SYSTEM_PROMPT
