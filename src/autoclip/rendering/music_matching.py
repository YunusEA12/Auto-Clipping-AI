"""Mood-aware scoring for process.pick_background_track().

background_music/tags.json maps a track filename to {"energy": 1-10, "moods": [...]}. A clip
is matched against those tags using signals already on the clip dict: the measured
energy_rating (analyze.py, from real audio energy), its hook_style and keywords in its
title/hook_explanation. The result is only a *weight* for the random pick: every tagged or
untagged track keeps a non-zero chance, so the epsilon-greedy learning in
optimization_engine.py can still collect samples for all of them.
"""

import json
import logging
import re
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

TAGS_FILENAME = "tags.json"

# Floor weight so a poor match is unlikely but never impossible.
MIN_WEIGHT = 0.1
NEUTRAL_SCORE = 0.5

# hook_style (analyze.HOOK_STYLES) -> moods that suit it.
HOOK_STYLE_MOODS = {
    "curiosity_gap": {"mysterious", "ambient", "chill"},
    "shocking_visual": {"intense", "dramatic", "energetic"},
    "direct_quote": {"chill", "neutral", "upbeat"},
    "list_open_loop": {"upbeat", "neutral", "energetic"},
    "in_media_res": {"energetic", "intense", "upbeat"},
    "other": set(),
}

# Title/hook keywords (DE + EN) -> mood. Matched on word starts, lowercase.
KEYWORD_MOODS = {
    "funny": ("lustig", "witzig", "lol", "funny", "fail", "troll", "haha"),
    "intense": ("clutch", "insane", "krass", "wahnsinn", "rage", "wut", "ace", "kill", "boss"),
    "dramatic": ("schock", "shock", "unglaublich", "unbelievable", "tragisch", "heartbreak"),
    "mysterious": ("geheimnis", "secret", "mystery", "warum", "why", "wieso", "unknown"),
    "chill": ("entspannt", "chill", "ruhig", "relax", "story", "geschichte"),
}
# "funny" content is best served by upbeat music.
MOOD_ALIASES = {"funny": {"upbeat", "funny"}}


def load_track_tags(music_dir: Path) -> dict:
    """Read tags.json; missing/corrupt file -> {} (picker falls back to untagged behaviour)."""
    path = Path(music_dir) / TAGS_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s, ignoring track tags: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def desired_moods(clip: Optional[dict]) -> set:
    """Moods a clip calls for, from its hook_style and title/hook text."""
    if not clip:
        return set()
    moods = set(HOOK_STYLE_MOODS.get(clip.get("hook_style") or "", set()))
    text = f"{clip.get('title', '')} {clip.get('hook_explanation', '')}".lower()
    words = re.findall(r"\w+", text)
    for mood, keywords in KEYWORD_MOODS.items():
        if any(w.startswith(k) for w in words for k in keywords):
            moods |= MOOD_ALIASES.get(mood, {mood})
    return moods


def score_track(tags: Optional[dict], clip: Optional[dict]) -> float:
    """0..1 match between a track's tags and a clip; NEUTRAL_SCORE when either is unknown."""
    if not tags or not clip:
        return NEUTRAL_SCORE

    parts = []
    energy = clip.get("energy_rating")
    track_energy = tags.get("energy")
    if isinstance(energy, (int, float)) and isinstance(track_energy, (int, float)):
        parts.append(max(0.0, 1.0 - abs(energy - track_energy) / 9.0))

    wanted = desired_moods(clip)
    track_moods = {str(m).lower() for m in (tags.get("moods") or [])}
    if wanted and track_moods:
        parts.append(len(wanted & track_moods) / len(wanted | track_moods) * 2)  # overlap, scaled up
    if not parts:
        return NEUTRAL_SCORE
    return min(1.0, sum(parts) / len(parts))


def track_weights(tracks: Iterable[Path], tags: dict, clip: Optional[dict]) -> list:
    """Selection weight per track (same order as `tracks`)."""
    return [max(MIN_WEIGHT, score_track(tags.get(t.name), clip)) for t in tracks]
