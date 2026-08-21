"""Persistent, content-hash-keyed ledger of what has been uploaded to which platform, and
what's currently in flight (2026-08-21) — closes two related, real production incidents:

1. A TikTok click that isn't confirmed leaves a clip in output/ for a retry, and retrying used
   to mean re-running the WHOLE multi-platform upload from scratch — re-posting to YouTube and
   Instagram too, even though those two had already genuinely succeeded on an earlier attempt
   at the exact same clip. Observed live: a single real clip ended up with 3 separate real
   YouTube videos and 3 separate real Instagram Reels for identical content before TikTok's
   click finally confirmed (first narrowly fixed via a per-clip output/ sidecar, commit
   0aa31b1 — this module replaces that narrower fix with a global one, see below).

2. That per-clip sidecar only protected a single clip's retry loop within one still-pending
   file in output/. It did nothing for the wider version of the same risk: a supervisor
   restart or a crashed worker mid-upload leaves no durable record that an upload to platform
   X was ever attempted, so the next run has no way to know whether it should retry or whether
   the previous attempt actually already went through — and two processes could in principle
   race on the same file at once (this pipeline runs one auto_pilot.py process per live
   streamer, all funneling into the same shared TikTok/YouTube/Instagram accounts).

Keyed by the video file's own content hash (sha256), not by filename or output path — the
most robust identifier available: immune to a clip being renamed/moved between output/ and
uploaded_clips/, and to two DIFFERENT clips coincidentally generating the same AI title (seen
live: "Nico testet Calisthenics" recurred across separate stream sessions on the same
streamer, which would have collided if this were keyed by title instead).

Three states per (content_hash, platform):
  - "pending"  — an attempt is in flight, or was interrupted before resolving either way.
                 Fresh (younger than PENDING_STALE_MINUTES): callers must not start a second,
                 concurrent attempt. Stale: something crashed and never resolved it; eligible
                 for a fresh attempt again — a pending marker must never be a life sentence.
  - "done"     — confirmed successful. Never re-attempted; callers should treat future upload
                 requests for this (hash, platform) pair as an immediate no-op success.
  - "failed"   — a genuine, confirmed failure (the platform's API/UI itself rejected it, not
                 just an ambiguous unconfirmed click). Eligible for a fresh retry immediately —
                 unlike "pending", there's nothing to wait out here.

All reads/writes happen under a single cross-process FileLock, the same pattern already used
by streamers.py for its own concurrent-write protection — several auto_pilot.py processes and
metrics_tracker.py all touch this file."""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock, Timeout

import atomic_io

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

LEDGER_PATH = Path("upload_ledger.json")
LEDGER_LOCK_TIMEOUT_SECONDS = 10

# How long a "pending" marker is trusted as "someone else is actively working on this" before
# it's treated as abandoned (a crashed process, a killed supervisor, a machine reboot mid-
# upload) and released for a fresh attempt. Comfortably longer than any single platform's own
# upload+confirmation timeout in this codebase (YouTube's resumable upload, TikTok/Instagram's
# Playwright flows) so a slow-but-genuinely-in-progress attempt is never preempted by a second
# worker, while a truly abandoned one doesn't block a clip from ever being retried.
PENDING_STALE_MINUTES = 30

HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB — large enough to be fast, small enough to bound memory


def compute_content_hash(video_path: Path) -> str:
    """sha256 of the file's actual bytes, streamed rather than loaded whole — a rendered clip
    can be tens of MB, and this runs once per upload attempt."""
    hasher = hashlib.sha256()
    with open(video_path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _ledger_lock() -> FileLock:
    return FileLock(str(LEDGER_PATH) + ".lock", timeout=LEDGER_LOCK_TIMEOUT_SECONDS)


def _load_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return {}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read %s — treating the ledger as empty rather than blocking every upload.", LEDGER_PATH)
        return {}


def _save_ledger(ledger: dict) -> None:
    atomic_io.atomic_write_json(LEDGER_PATH, ledger)


def get_entry(content_hash: str, platform: str) -> Optional[dict]:
    ledger = _load_ledger()
    return ledger.get(content_hash, {}).get(platform)


def is_done(content_hash: str, platform: str) -> bool:
    entry = get_entry(content_hash, platform)
    return bool(entry) and entry.get("status") == "done"


def try_mark_pending(content_hash: str, platform: str, title: str = "") -> bool:
    """Atomically checks AND reserves a (content_hash, platform) slot under the ledger's file
    lock. Returns True if the caller may proceed with a real upload attempt right now; False
    if another attempt is already fresh-pending (a concurrent worker, or a very recent attempt
    by this same process that hasn't resolved yet) — the caller must NOT start a second upload
    in that case.

    The check-then-write happens in one lock acquisition, not two separate steps — reading
    "is anything pending" and then separately writing "now something is pending" would let two
    processes both read "nothing pending" before either had written anything, and both proceed
    — exactly the duplicate-submission race this function exists to close.

    Also returns True (reserves fresh) for "done"/"failed" entries being re-attempted — this
    function only ever gates concurrent/duplicate PENDING attempts; callers are expected to
    have already checked is_done() themselves before ever reaching here (done entries should
    short-circuit before attempting anything at all, not merely reserve a new pending slot for
    something that doesn't need re-uploading)."""
    try:
        with _ledger_lock():
            ledger = _load_ledger()
            platform_entries = ledger.setdefault(content_hash, {})
            existing = platform_entries.get(platform)

            if existing and existing.get("status") == "pending":
                started_at_raw = existing.get("updated_at")
                try:
                    started_at = datetime.fromisoformat(started_at_raw) if started_at_raw else None
                except ValueError:
                    started_at = None
                if started_at is not None and datetime.now(timezone.utc) - started_at < timedelta(minutes=PENDING_STALE_MINUTES):
                    return False
                logger.warning(
                    "Releasing a stale 'pending' %s entry for hash %s (title: %r) — older than "
                    "%d minutes with no resolution, likely an interrupted attempt from a crashed "
                    "process or supervisor restart. Allowing a fresh attempt.",
                    platform, content_hash[:12], title, PENDING_STALE_MINUTES,
                )

            platform_entries[platform] = {
                "status": "pending",
                "title": title,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_ledger(ledger)
            return True
    except Timeout:
        logger.warning(
            "Could not acquire the upload ledger lock within %ds — proceeding without a "
            "reservation rather than blocking every upload indefinitely. A duplicate is "
            "possible in this specific case (concurrent lock contention), though rare.",
            LEDGER_LOCK_TIMEOUT_SECONDS,
        )
        return True


def _set_status(content_hash: str, platform: str, status: str, **extra) -> None:
    try:
        with _ledger_lock():
            ledger = _load_ledger()
            entry = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
            entry.update(extra)
            ledger.setdefault(content_hash, {})[platform] = entry
            _save_ledger(ledger)
    except Timeout:
        logger.warning(
            "Could not acquire the upload ledger lock within %ds to record a '%s' result for "
            "%s on hash %s — this specific outcome won't be remembered, so a future attempt "
            "may re-run unnecessarily (safe, just wasteful) rather than being lost as a false "
            "'never attempted'.", LEDGER_LOCK_TIMEOUT_SECONDS, status, platform, content_hash[:12],
        )


def mark_done(content_hash: str, platform: str, **extra) -> None:
    _set_status(content_hash, platform, "done", **extra)


def mark_failed(content_hash: str, platform: str, detail: str = "") -> None:
    _set_status(content_hash, platform, "failed", detail=detail)


def mark_unresolved(content_hash: str, platform: str, detail: str = "") -> None:
    """For the ambiguous case a platform's own automation can return: the action that would
    publish the content genuinely happened (a click, an API call) but the signal confirming it
    actually went live was never observed (TikTok/Instagram's Playwright flows both
    distinguish success from confirmed for exactly this reason). Deliberately left as
    "pending" rather than resolved either way — NOT "done" (unconfirmed is not proof of
    success) and NOT "failed" (an immediate retry could double-post something that actually
    already succeeded). Subject to the same PENDING_STALE_MINUTES cooldown as a real in-flight
    attempt before a caller is allowed to try again — the direct implementation of "don't
    immediately retry without verifying published status" for the two platforms
    (TikTok/Instagram) that have no cheap, official API to actually verify that against."""
    _set_status(content_hash, platform, "pending", detail=detail)
