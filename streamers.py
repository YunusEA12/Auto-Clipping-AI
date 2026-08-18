"""Fleet configuration: a persistent list of streamers to watch 24/7 (name, stream URL,
which "Streamer-Mitarbeiter" profile to use, whether to auto-upload survivors to TikTok).

Shared by orchestrator.py (the watch daemon, which reads it every poll cycle) and app.py's
"👥 Streamer Verwaltung & Fleet" tab (which reads AND writes it) — one place for the JSON
shape and validation so neither has to re-implement the other's parsing."""

import json
import logging
from pathlib import Path
from typing import List

from pydantic import BaseModel

import atomic_io

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

STREAMERS_PATH = Path("streamers.json")


class StreamerEntry(BaseModel):
    name: str
    url: str
    profile: str = ""
    auto_upload: bool = False


def load_streamers(path: Path = None) -> List[dict]:
    # `path: Path = None`, resolved to STREAMERS_PATH dynamically below, not bound as a
    # default-argument value: a plain `path: Path = STREAMERS_PATH` default captures the
    # value once at function-definition time, so monkeypatching streamers.STREAMERS_PATH in
    # a test (or reassigning it anywhere at runtime) would silently have no effect on calls
    # that rely on the default — every function below in this file follows the same pattern.
    if path is None:
        path = STREAMERS_PATH
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Could not read %s: %s", path, e)
        return []

    if not isinstance(raw, list):
        logger.error("%s must contain a JSON list of streamer objects", path)
        return []

    entries = []
    for item in raw:
        try:
            entries.append(StreamerEntry(**item).model_dump())
        except Exception as e:
            logger.warning("Skipping invalid streamer entry %r in %s: %s", item, path, e)
    return entries


def save_streamers(entries: List[dict], path: Path = None) -> None:
    if path is None:
        path = STREAMERS_PATH
    validated = [StreamerEntry(**e).model_dump() for e in entries]
    atomic_io.atomic_write_json(path, validated)
    logger.info("Saved %d streamer(s) to %s", len(validated), path)


def add_streamer(
    name: str, url: str, profile: str = "", auto_upload: bool = False, path: Path = None
) -> None:
    if path is None:
        path = STREAMERS_PATH
    entries = load_streamers(path)
    if any(e["name"] == name for e in entries):
        raise ValueError(f"Streamer '{name}' existiert bereits.")

    entries.append(StreamerEntry(name=name, url=url, profile=profile, auto_upload=auto_upload).model_dump())
    save_streamers(entries, path)


def remove_streamer(name: str, path: Path = None) -> bool:
    """Returns True if a streamer was actually removed, False if `name` wasn't found."""
    if path is None:
        path = STREAMERS_PATH
    entries = load_streamers(path)
    remaining = [e for e in entries if e["name"] != name]
    if len(remaining) == len(entries):
        return False

    save_streamers(remaining, path)
    return True
