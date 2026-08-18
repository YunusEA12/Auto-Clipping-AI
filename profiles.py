"""Streamer profile management ("Streamer-Mitarbeiter"): per-creator config (context,
trigger words, energy threshold, default stream URL) loaded from profiles/<name>.json."""

import json
import logging
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field, ValidationError

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


class ProfileCorruptError(Exception):
    """Raised when a profile file exists but its content is unusable (invalid JSON or fails
    schema validation) — distinct from FileNotFoundError so callers can tell "missing" apart
    from "present but broken"."""


def load_profile(profile_name: str) -> StreamerProfile:
    path = profile_path(profile_name)
    if not path.exists():
        raise FileNotFoundError(
            f"Streamer profile not found: {path}. Available profiles: {list_profiles()}"
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = StreamerProfile(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        raise ProfileCorruptError(f"Streamer profile {path} is present but invalid: {e}") from e

    logger.info(
        "Loaded streamer profile '%s' (energy_threshold=%d, %d trigger word(s))",
        profile.name, profile.energy_threshold, len(profile.trigger_words),
    )
    return profile


def load_profile_or_fallback(profile_name: str, fallback: str = DEFAULT_FALLBACK_PROFILE) -> StreamerProfile:
    """Like load_profile(), but never crashes the caller (e.g. auto_pilot.py's --profile
    startup, called outside any try/except) over a missing/misspelled profile name OR a
    profile file that exists but is malformed (invalid JSON, fails schema validation): logs a
    clear warning and falls back to `fallback` (default_streamer), or the first available
    profile if even that is missing/broken. Only raises if no usable profile at all can be
    found — a genuine "nothing to fall back to" case, not a routine startup hiccup."""
    try:
        return load_profile(profile_name)
    except (FileNotFoundError, ProfileCorruptError) as e:
        available = [p for p in list_profiles() if p != profile_name]
        logger.warning(
            "Streamer-Profil '%s' nicht nutzbar (%s). Verfügbare Profile: %s",
            profile_name, e, available or "keine",
        )

        if fallback != profile_name and fallback in available:
            try:
                logger.warning("Verwende Fallback-Profil '%s'.", fallback)
                return load_profile(fallback)
            except (FileNotFoundError, ProfileCorruptError) as fallback_error:
                logger.warning("Fallback-Profil '%s' ebenfalls nicht nutzbar: %s", fallback, fallback_error)
                available = [p for p in available if p != fallback]

        for candidate in available:
            try:
                logger.warning("Verwende stattdessen Profil '%s'.", candidate)
                return load_profile(candidate)
            except (FileNotFoundError, ProfileCorruptError) as candidate_error:
                logger.warning("Profil '%s' ebenfalls nicht nutzbar: %s", candidate, candidate_error)
                continue

        raise FileNotFoundError(
            f"Kein nutzbares Streamer-Profil verfügbar (weder '{profile_name}' noch ein "
            f"Fallback) in {PROFILES_DIR}."
        )


def list_profiles() -> List[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
