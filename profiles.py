"""Streamer profile management ("Streamer-Mitarbeiter"): per-creator config (context,
trigger words, energy threshold, default stream URL) loaded from profiles/<name>.json."""

import json
import logging
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFILES_DIR = Path("profiles")
DEFAULT_FALLBACK_PROFILE = "default_streamer"


class StreamerProfile(BaseModel):
    name: str
    stream_url: str = ""
    energy_threshold: int = Field(default=7, ge=1, le=10)
    trigger_words: List[str] = Field(default_factory=list)
    context_prompt: str = ""


def profile_path(profile_name: str) -> Path:
    return PROFILES_DIR / f"{profile_name}.json"


def load_profile(profile_name: str) -> StreamerProfile:
    path = profile_path(profile_name)
    if not path.exists():
        raise FileNotFoundError(
            f"Streamer profile not found: {path}. Available profiles: {list_profiles()}"
        )

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    profile = StreamerProfile(**data)
    logger.info(
        "Loaded streamer profile '%s' (energy_threshold=%d, %d trigger word(s))",
        profile.name, profile.energy_threshold, len(profile.trigger_words),
    )
    return profile


def load_profile_or_fallback(profile_name: str, fallback: str = DEFAULT_FALLBACK_PROFILE) -> StreamerProfile:
    """Like load_profile(), but never crashes the caller (e.g. stream_watcher.py's --profile
    startup) over a missing/misspelled profile name: logs a clear warning and falls back to
    `fallback` (default_streamer), or the first available profile if even that is missing.
    Only raises if no profile at all can be found."""
    try:
        return load_profile(profile_name)
    except FileNotFoundError:
        available = list_profiles()
        logger.warning(
            "Streamer-Profil '%s' nicht gefunden. Verfügbare Profile: %s",
            profile_name, available or "keine",
        )

        if fallback != profile_name and fallback in available:
            logger.warning("Verwende Fallback-Profil '%s'.", fallback)
            return load_profile(fallback)

        if available:
            next_best = available[0]
            logger.warning(
                "Fallback-Profil '%s' ebenfalls nicht verfügbar, verwende stattdessen '%s'.",
                fallback, next_best,
            )
            return load_profile(next_best)

        raise FileNotFoundError(
            f"Kein Streamer-Profil verfügbar (weder '{profile_name}' noch ein Fallback) in {PROFILES_DIR}."
        )


def list_profiles() -> List[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
