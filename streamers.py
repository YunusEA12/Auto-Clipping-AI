"""Fleet configuration: a persistent list of streamers to watch 24/7 (name, stream URL,
which "Streamer-Mitarbeiter" profile to use, whether to auto-upload survivors to TikTok, and
whether to actually publish them live).

Shared by orchestrator.py (the watch daemon, which reads it every poll cycle) and app.py's
"👥 Streamer Verwaltung & Fleet" tab (which reads AND writes it) — one place for the JSON
shape and validation so neither has to re-implement the other's parsing.

auto_upload and publish are separate fields, not one: TikTok's web upload flow has no
draft-save action anymore (confirmed 2026-08-18 — an abandoned upload is discarded, not
saved), so auto_upload=true with publish=false means Phase 5 (Deployment) is skipped
entirely for that streamer — there's no safe partial action to take. publish=true is the
only thing that makes auto_upload actually do anything; keeping them separate (rather than
collapsing into one flag) keeps that an explicit, visible choice per streamer rather than a
default nobody consciously picked."""

import json
import logging
from pathlib import Path
from typing import List

from filelock import FileLock, Timeout
from pydantic import BaseModel

import atomic_io

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

STREAMERS_PATH = Path("streamers.json")

# add_streamer/remove_streamer are each a read-modify-write (load_streamers() then
# save_streamers()) with no atomicity across the two calls — app.py's dashboard can, in
# principle, handle two concurrent edits (two browser tabs, or a Streamlit rerun racing a
# background action), and without a lock the second save silently clobbers the first's
# change: both reads see the same list, both write back their own version, whichever finishes
# last wins (found in review, 2026-08-18). atomic_write_json still guarantees the file itself
# is never left corrupted/torn — this closes the separate "lost update" gap on top of that.
STREAMERS_LOCK_TIMEOUT_SECONDS = 10


def _streamers_lock(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock", timeout=STREAMERS_LOCK_TIMEOUT_SECONDS)


class StreamerEntry(BaseModel):
    name: str
    url: str
    profile: str = ""
    auto_upload: bool = False
    publish: bool = False


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
    name: str, url: str, profile: str = "", auto_upload: bool = False, publish: bool = False,
    path: Path = None,
) -> None:
    if path is None:
        path = STREAMERS_PATH
    try:
        with _streamers_lock(path):
            entries = load_streamers(path)
            if any(e["name"] == name for e in entries):
                raise ValueError(f"Streamer '{name}' existiert bereits.")

            entries.append(
                StreamerEntry(name=name, url=url, profile=profile, auto_upload=auto_upload, publish=publish).model_dump()
            )
            save_streamers(entries, path)
    except Timeout:
        raise RuntimeError(f"Could not acquire lock on {path} within {STREAMERS_LOCK_TIMEOUT_SECONDS}s")


def remove_streamer(name: str, path: Path = None) -> bool:
    """Returns True if a streamer was actually removed, False if `name` wasn't found."""
    if path is None:
        path = STREAMERS_PATH
    try:
        with _streamers_lock(path):
            entries = load_streamers(path)
            remaining = [e for e in entries if e["name"] != name]
            if len(remaining) == len(entries):
                return False

            save_streamers(remaining, path)
            return True
    except Timeout:
        raise RuntimeError(f"Could not acquire lock on {path} within {STREAMERS_LOCK_TIMEOUT_SECONDS}s")
