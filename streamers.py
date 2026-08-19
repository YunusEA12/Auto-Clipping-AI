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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from filelock import FileLock, Timeout
from pydantic import BaseModel

import atomic_io

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

STREAMERS_PATH = Path("streamers.json")

# Same glob auto_pilot.py's --streamer-name namespacing (H-14) and dashboard_api.py's
# list_agent_state_paths() already use by convention — kept as a literal here too rather
# than importing auto_pilot.py, which pulls in ingest/analyze/train_loop/tiktok_uploader and
# everything THEY depend on, far heavier than orchestrator.py/process_supervisor.py (both
# meant to stay minimal top-level scripts) need just to glob a filename pattern.
AGENT_STATE_GLOB = "agent_state*.json"
AGENT_STATE_MAX_AGE_HOURS = 24


def _slugify(text: str) -> str:
    """Minimal copy of process.slugify() — duplicated rather than imported, same reasoning
    as AGENT_STATE_GLOB above: process.py's own import chain (cv2, mediapipe via vision.py)
    is far heavier than this module should need just to compare a streamer name against an
    agent_state_<slug>.json filename."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return slug or "unknown"

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
    seen_names = set()
    for item in raw:
        try:
            entry = StreamerEntry(**item).model_dump()
        except Exception as e:
            logger.warning("Skipping invalid streamer entry %r in %s: %s", item, path, e)
            continue
        # add_streamer() already rejects a duplicate name at write time, and the filelock
        # above (M-15 in the audit) closes the race that could otherwise let two concurrent
        # adds both pass that check — but neither guards against someone hand-editing
        # streamers.json directly. A duplicate name here is exactly what produced the
        # dashboard's "papaplatte twice" symptom reported live, 2026-08-19 (in that case from
        # a stale leftover agent_state.json, not a real duplicate here, but the same class of
        # bug is trivial to reintroduce by hand-editing this file, so guard it at the source
        # too): keep the first occurrence, drop the rest, loud about it.
        if entry["name"] in seen_names:
            logger.warning(
                "Duplicate streamer name '%s' in %s — keeping the first entry, dropping this one",
                entry["name"], path,
            )
            continue
        seen_names.add(entry["name"])
        entries.append(entry)
    return entries


def purge_stale_agent_states(
    streamers_path: Path = None, max_age_hours: float = AGENT_STATE_MAX_AGE_HOURS, root: Path = None,
) -> List[str]:
    """Deletes agent_state*.json files (auto_pilot.py's per-streamer dashboard state, see
    H-14 in the audit) that are either orphaned — their slug no longer matches any streamer
    currently in streamers.json, e.g. a removed streamer's last-known state lingering
    forever — or stale — no update in over `max_age_hours`, e.g. a subprocess that crashed
    hard enough to never reach its own error-cooldown update. Found live, 2026-08-19: a
    leftover flat agent_state.json from before --streamer-name was threaded through
    everywhere sat next to the real agent_state_papaplatte.json, both showing "papaplatte" in
    the dashboard at once.

    The legacy flat agent_state.json (written by a manual/standalone run with no
    --streamer-name) has no slug to check against streamers.json, so it can't be orphaned by
    name the way a per-streamer file can — but its mere COEXISTENCE with any per-streamer
    agent_state_<slug>.json file is itself sufficient evidence it's obsolete, independent of
    age: a single auto_pilot.py process only ever writes to one target, and orchestrator.py
    always passes --streamer-name now, so a live system never legitimately writes both at
    once. Without this, the exact bug found live would have sat there for another ~15 hours
    before the age-based staleness check alone caught up to it. It's still also subject to
    the ordinary staleness check below, for the genuinely-standalone case where no
    per-streamer files exist at all.

    A file with no parseable last_updated is left alone rather than guessed at, same
    never-prune-on-ambiguity principle as metrics_tracker.prune_viral_memory.

    Returns the filenames actually deleted, for the caller to log."""
    if root is None:
        root = Path(".")

    current_slugs = {_slugify(e["name"]) for e in load_streamers(streamers_path)}
    now = datetime.now(timezone.utc)
    deleted: List[str] = []

    paths = sorted(root.glob(AGENT_STATE_GLOB))
    any_namespaced_state_exists = any(p.stem != "agent_state" for p in paths)

    for path in paths:
        stem = path.stem
        slug = stem[len("agent_state_"):] if stem != "agent_state" and stem.startswith("agent_state_") else None

        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

        if slug is None:
            orphaned = any_namespaced_state_exists
        else:
            orphaned = slug not in current_slugs

        stale = False
        last_updated_raw = state.get("last_updated")
        if last_updated_raw:
            try:
                age_hours = (now - datetime.fromisoformat(last_updated_raw)).total_seconds() / 3600
                stale = age_hours > max_age_hours
            except ValueError:
                stale = False

        if not (orphaned or stale):
            continue

        if orphaned:
            reason = "superseded by per-streamer state files" if slug is None else "orphaned (no matching streamer)"
        else:
            reason = f"stale (>{max_age_hours:.0f}h since last update)"
        try:
            path.unlink()
            deleted.append(path.name)
            logger.info("🧹 Purged %s: %s", path.name, reason)
        except OSError as e:
            logger.warning("Could not purge %s: %s", path.name, e)

    return deleted


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
