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


def list_profiles() -> List[str]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
