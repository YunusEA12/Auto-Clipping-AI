"""Autonomous, self-improving agent loop: Collect -> Evaluate -> Purge -> Deploy -> Repeat,
forever.

Each cycle:
  1. Collect    — run the existing ingest -> transcribe -> analyze -> process pipeline to
     render a fresh batch of 3-5 clips into output/.
  2. Evaluate   — hand that batch (with the rendered .mp4 paths) to train_loop.py's
     multimodal critic, which scores each clip on narrative AND visual composition
     (reward_score -10..+10, using extracted preview frames when available — degrading to
     text-only if the vision call fails) and derives new content/visual/viral-pattern rules
     into ai_guidelines.txt.
  3. Purge      — physically delete any clip (its .mp4 AND its entry in the batch's
     *_clips.json) whose reward_score falls below --purge-threshold. Only "winners" survive
     on disk.
  5. Deployment — with --auto-upload: every surviving clip is handed to tiktok_uploader.py
     (a cookie-authenticated Playwright bot) and, once uploaded, moved out of output/ into
     uploaded_clips/ (with a metadata sidecar .json) to keep the working directory limited
     to undecided clips. metrics_tracker.py later reads that sidecar to build
     viral_memory.json, which the critic reads back in on future runs.
  6. Repeat     — go back to step 1. analyze.py re-reads ai_guidelines.txt fresh on every
     call (see analyze.load_ai_guidelines_section()), so each new cycle's clip selection is
     informed by everything the critic has learned so far.

This script does not duplicate any pipeline logic — it only orchestrates the existing
ingest.py / transcribe.py / analyze.py / process.py / train_loop.py / stream_watcher.py /
tiktok_uploader.py functions in a loop.

Usage:
    python auto_pilot.py --video some_vod.mp4                    # reprocess one local VOD repeatedly
    python auto_pilot.py --url https://youtube.com/watch?v=...    # download once, reprocess repeatedly
    python auto_pilot.py --live --url https://twitch.tv/<channel> # record a fresh chunk every cycle
    python auto_pilot.py --profile eliasn97 --live                # same, via a streamer profile
    python auto_pilot.py --profile eliasn97 --live --auto-upload  # also deploy survivors to TikTok (as drafts)
"""

import argparse
import json
import logging
import random
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import analyze
import atomic_io
import ingest
import process as process_module
import profiles
import stream_watcher
import tiktok_uploader
import train_loop
import transcribe
import upload_manager

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

BATCH_SIZE_MIN = 3
BATCH_SIZE_MAX = 5

# Caps how many backlog clips (see find_backlog_clips) get swept into a single cycle's
# deployment batch. Without this, a streamer that was down for a while (VPS reboot, a long
# upload outage) would dump its entire backlog into one deployment pass the moment it comes
# back — the same burst-of-uploads risk BATCH_SIZE_MAX already guards against for freshly
# rendered clips, just for the backlog path instead.
BACKLOG_BATCH_LIMIT = BATCH_SIZE_MAX

# 2026-08-20: nudged 0 -> -2 after a live funnel audit found only ~52% of rendered clips
# survived the critic pass (kept 12 / deleted 11 across the sampled window) — the second-
# biggest source of attrition in the pipeline after the duration-bounds filter (see
# analyze.MIN_CLIP_DURATION's own comment for that fix). Part of that 48% loss was very
# likely the critic's visual-composition rubric penalizing correctly-classified full_cam/
# blur_background clips for "missing gameplay" (also fixed the same day — see
# train_loop.CRITIC_SYSTEM_PROMPT). This gives genuinely borderline content (a decent clip
# with one minor quibble, scored -1 or -2) room to survive, while still purging anything the
# critic actually considers bad (-3 and below) — a small, reversible nudge, not a removal of
# the quality gate.
#
# 2026-08-22: nudged again, -2 -> -3. The critic was still marking down short, fast-paced
# Twitch reaction/punchline clips for "lacking context" or "no resolved end" purely because it
# was only ever shown the clip's own isolated transcript slice — genuinely fine short clips
# were landing at -3/-4 for that reason alone. Now that build_critic_user_content() also feeds
# the critic the lead-in transcript text before each clip (see train_loop.CRITIC_CONTEXT_
# LOOKBACK_SECONDS) and CRITIC_SYSTEM_PROMPT explicitly tells it not to penalize brevity, most
# of that false-negative mass should land closer to 0 anyway — this one more small step of
# floor room is for whatever still lands slightly negative on a genuine but minor quibble.
DEFAULT_PURGE_THRESHOLD = -3
DEFAULT_COOLDOWN_SECONDS = 30
DEFAULT_ERROR_COOLDOWN_SECONDS = 90

# 2026-08-21: found in a production health-check audit — output/<streamer>/ for a streamer
# configured auto_upload=True, publish=False never gets touched by anything. It's not
# low-scoring (purge_low_scoring_clips() only removes clips BELOW the purge threshold, not
# high-scoring survivors) and run_deployment_phase() is never called in this configuration
# (see the "Deployment übersprungen" branch below) — so these clips accumulate forever with
# zero eviction. On a VPS where ProtectSystem=strict limits writes to a single partition
# (/opt/auto-clipping-ai), this eventually fills the disk and breaks rendering for every
# OTHER streamer sharing it too, not just this one. 14 days is long enough for a human to
# actually go review them (the whole point of this configuration), short enough not to let
# months of unwatched clips pile up unattended.
OUTPUT_RETENTION_DAYS = 14

# Where successfully-uploaded clips are archived to, out of output/ — keeps the working
# directory limited to clips still awaiting a decision (upload or the next purge pass).
UPLOADED_CLIPS_DIR = Path("uploaded_clips")

# Telemetry file the Streamlit "Agent Control Center" dashboard (app.py) polls to show what
# this process is doing right now — there's no direct connection between the two processes,
# just this JSON file on disk.
AGENT_STATE_PATH = Path("agent_state.json")

# Set once in main() when --streamer-name is given (i.e. orchestrator.py launched this as
# one of several concurrent streamer subprocesses) — every update_agent_state() call then
# writes to a per-streamer file instead of the single shared AGENT_STATE_PATH. Two streamers
# recording at once used to both read-modify-write the exact same file: whichever process's
# partial update dict omitted a field kept whatever the OTHER streamer's last write left
# there, so the dashboard could show one streamer's target_streamer next to another's
# clips_kept_total — guaranteed, not a corner case, whenever 2+ streamers were live (found in
# review, 2026-08-18: H-14). A module-level override (not a threaded parameter) because
# update_agent_state() already has ~10 call sites across this file using **updates only.
_agent_state_path_override: Optional[Path] = None
# Same idea for rendered-clip output — see process.render_clip()'s docstring.
_output_dir_override: Optional[Path] = None


def _resolve_agent_state_path() -> Path:
    return _agent_state_path_override or AGENT_STATE_PATH


def update_agent_state(**updates) -> dict:
    """Merge `updates` into this process's agent state file and write it back. Called at
    every phase transition in the loop below so app.py's Live Radar tab always reflects the
    agent's current phase, not just its state at the end of a cycle."""
    path = _resolve_agent_state_path()
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    state.update(updates)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    try:
        atomic_io.atomic_write_json(path, state)
    except OSError as e:
        logger.warning("Could not write %s: %s", path, e)

    return state


def _trim_to_batch(clips_path: Path, batch_size: int) -> list:
    """Keep only the top `batch_size` candidates (by viral_score, then energy_rating) from
    the clips analyze.py just found, and rewrite the file to just that batch — so Phase 1
    produces a bounded, high-signal batch instead of rendering everything the LLM found."""
    with open(clips_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    ranked = sorted(
        data.get("clips", []),
        key=lambda c: (c.get("viral_score", 0), c.get("energy_rating", 0)),
        reverse=True,
    )
    batch = ranked[:batch_size]

    atomic_io.atomic_write_json(clips_path, {"clips": batch})

    return batch


def purge_old_local_only_clips(output_dir: Path, retention_days: int = OUTPUT_RETENTION_DAYS) -> int:
    """Deletes each .mp4 (+ its render-metadata .json sidecar) in `output_dir` whose file
    mtime is older than `retention_days` — see OUTPUT_RETENTION_DAYS's own comment for why
    this exists at all. Age is judged by the file's own mtime, not any "rendered_at" field
    inside the sidecar, so this still works even if the sidecar is missing or corrupt.

    Best-effort: a single file that can't be deleted (e.g. a permissions hiccup) is logged
    and skipped rather than aborting the whole pass — this runs once per cycle for a
    streamer that's otherwise working fine, so it must never be the thing that breaks it.
    Returns the number of clips deleted."""
    if not output_dir.exists():
        return 0

    cutoff = time.time() - retention_days * 86400
    deleted = 0
    for mp4_path in output_dir.glob("*.mp4"):
        try:
            if mp4_path.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        for path in (mp4_path, mp4_path.with_suffix(".json")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning("Could not delete old local-only clip file %s: %s", path, e)
        deleted += 1

    if deleted:
        logger.info(
            "🧹 %d alte lokale Clip(s) in %s gelöscht (älter als %d Tage, nie veröffentlicht "
            "— auto_upload an, publish aus)",
            deleted, output_dir, retention_days,
        )
    return deleted


def find_backlog_clips(
    output_dir: Path, exclude: set,
) -> List[Tuple[dict, Path, Optional[int]]]:
    """Phase 5 (Deployment) only ever sees clips THIS cycle just rendered (survivors is built
    from this cycle's own `rendered`/`batch`) — a clip that survived a PAST cycle's Phase 3
    purge but never made it into uploaded_clips/ (the process was killed or the VPS rebooted
    mid-cycle, an upload attempt failed and outlived that cycle, ...) is otherwise invisible to
    every later cycle forever: analyze.analyze() writes a fresh clips.json each cycle, and
    purge_old_local_only_clips() only runs for auto_upload-without-publish configs (see its own
    comment) — never for the auto_upload+publish configs this actually matters for. Left alone,
    output/ grows one orphaned clip at a time for a streamer that's supposed to be fully live.

    Only surfaces clips whose render sidecar carries a reward_score (see
    _persist_reward_score) — proof the clip already passed Phase 3's critic gate, not merely
    that an .mp4 happens to exist. A clip whose process died BEFORE scoring has no such proof
    and is deliberately left alone here rather than skipping the quality gate to force it
    through; it's still visible for manual review, and simply persists in output/ until either
    a human clears it or a future cycle's own purge logic is extended to cover it.

    `exclude` is this cycle's own freshly-rendered paths (already handled as ordinary
    survivors), so nothing is ever double-counted or double-uploaded within one cycle.

    Returned newest-first by the clip's own rendered_at (2026-08-25, account-owner request):
    a freshly-live moment is far more time-sensitive than a clip that's already been sitting
    in this backlog for a while — the older one stays relevant regardless of which cycle
    finally gets to it, but a "reacting live right now" clip loses most of its value if it
    publishes hours late behind a long backlog queue. See _backlog_sort_key()'s own docstring
    for the fallback when rendered_at is missing (older sidecars)."""
    if not output_dir.exists():
        return []

    backlog: List[Tuple[dict, Path, Optional[int]]] = []
    for mp4_path in sorted(output_dir.glob("*.mp4")):
        if mp4_path in exclude:
            continue
        sidecar_path = mp4_path.with_suffix(".json")
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                clip = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if "reward_score" not in clip:
            continue
        backlog.append((clip, mp4_path, clip.get("reward_score")))

    backlog.sort(key=lambda item: _backlog_sort_key(item[0], item[1]), reverse=True)
    return backlog


def _backlog_sort_key(clip: dict, mp4_path: Path) -> float:
    """Newest-first ordering key for find_backlog_clips() — the sidecar's own rendered_at
    timestamp when present (the authoritative "when this clip was actually created" signal),
    falling back to the .mp4 file's own mtime for older sidecars written before that field
    existed, or an unparseable value. Returns a POSIX timestamp for sorting; never raises."""
    rendered_at = clip.get("rendered_at")
    if rendered_at:
        try:
            return datetime.fromisoformat(rendered_at).timestamp()
        except ValueError:
            pass
    try:
        return mp4_path.stat().st_mtime
    except OSError:
        return 0.0


def _persist_reward_score(output_path: Path, score: Optional[int]) -> None:
    """Folds the critic's reward_score into the render-time metadata sidecar (written by
    process._write_clip_metadata_sidecar before scoring ever happened, so it can't include
    this on its own). Without this, a stray .mp4 sitting in output/ from an interrupted past
    cycle is indistinguishable from one whose process died between rendering and scoring —
    both are just "an .mp4 with a sidecar that has no reward_score". find_backlog_clips()
    relies on this field's presence as proof a clip already passed Phase 3, not just that it
    exists. Best-effort, same contract as the sidecar's own initial write: never fails the
    cycle it's describing."""
    if score is None:
        return
    sidecar_path = output_path.with_suffix(".json")
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not persist reward_score into sidecar %s: %s", sidecar_path, e)
        return
    data["reward_score"] = score
    atomic_io.atomic_write_json(sidecar_path, data)


# Caps how many uploaded_clips/ entries get a YouTube retry attempt per cycle — same
# burst-avoidance reasoning as BACKLOG_BATCH_LIMIT, just for a YouTube-specific outage (quota
# exceeded, a stale OAuth token) instead of a whole-pipeline one.
YOUTUBE_RETRY_BATCH_LIMIT = BATCH_SIZE_MAX

# Same reasoning as YOUTUBE_RETRY_BATCH_LIMIT, for Instagram's equivalent backlog sweep — see
# find_missing_instagram_uploads()'s own docstring.
INSTAGRAM_RETRY_BATCH_LIMIT = BATCH_SIZE_MAX


def find_missing_youtube_uploads(uploaded_clips_dir: Path, streamer_name: Optional[str] = None) -> List[Path]:
    """upload_manager.upload_clip_everywhere() attempts TikTok and YouTube independently per
    clip (see its own module docstring: "a YouTube failure never crashes the calling cycle,
    and never blocks the TikTok result") — but only TikTok's outcome decides whether a clip
    moves into uploaded_clips/ at all (run_deployment_phase's own docstring: "based on
    TikTok's result alone"). That means a clip whose YouTube leg failed (quota exceeded, a
    transient API error, an expired token) or wasn't attempted (an old sidecar predating the
    YouTube leg entirely) has no automatic retry once it's archived here: find_backlog_clips()
    only ever looks at output/, never uploaded_clips/. This scans uploaded_clips/ for exactly
    that gap — clips whose sidecar shows `publish: true` (upload was genuinely intended, not
    e.g. a manual-mode/publish=False artifact) but `youtube_uploaded` isn't `true`.

    `streamer_name`, when given, scopes the scan to clips whose sidecar's own `streamer_name`
    either matches or is absent (2026-08-21: uploaded_clips/ is a single flat directory shared
    by every concurrent streamer process — found in review, unlike output/<streamer>/, it was
    never namespaced — so without this filter, every streamer's own retry cycle was scanning
    and attempting the ENTIRE fleet's shared backlog, not just its own clips). A sidecar with
    no `streamer_name` at all (archived before this field existed, or a manual/single-video run
    with no --streamer-name) is treated as "unowned" and always included, rather than orphaned
    by a filter that can't attribute it to anyone.

    Returns sidecar-having .mp4 paths only — a clip missing its metadata (a write failure at
    upload time) has no title/description/hashtags to retry with and is left for manual
    review rather than guessed at."""
    if not uploaded_clips_dir.exists():
        return []

    missing: List[Path] = []
    for mp4_path in sorted(uploaded_clips_dir.glob("*.mp4")):
        sidecar_path = mp4_path.with_suffix(".json")
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        clip_owner = data.get("streamer_name")
        if streamer_name is not None and clip_owner is not None and clip_owner != streamer_name:
            continue
        if data.get("publish") and not data.get("youtube_uploaded"):
            missing.append(mp4_path)
    return missing


def retry_missing_youtube_uploads(uploaded_clips_dir: Path, streamer_name: Optional[str] = None) -> Tuple[int, int]:
    """Retries ONLY the YouTube leg for clips found by find_missing_youtube_uploads() — never
    TikTok, since that already succeeded and re-running tiktok_uploader.try_upload_clip()
    would post a second, duplicate, publicly-visible copy of a clip that's already live.
    Uses upload_manager._upload_to_youtube() directly (not upload_clip_everywhere()) for
    exactly that reason — there's no TikTok leg to run here at all.

    `streamer_name` is passed straight through to find_missing_youtube_uploads() — see its own
    docstring for why this process should only retry its own streamer's clips out of the
    shared uploaded_clips/ backlog, not the whole fleet's.

    Returns (retried_ok, retried_failed). Best-effort per clip: a sidecar write failure after
    a successful upload is logged, not raised — the YouTube upload already happened by then,
    so raising would make a real success look like a failure to the caller."""
    candidates = find_missing_youtube_uploads(uploaded_clips_dir, streamer_name)[:YOUTUBE_RETRY_BATCH_LIMIT]
    ok, failed = 0, 0

    for i, mp4_path in enumerate(candidates, start=1):
        sidecar_path = mp4_path.with_suffix(".json")
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Heartbeat (2026-08-24 dashboard-stall fix) — see run_deployment_phase's own comment.
        update_agent_state(current_action=f"📺 YouTube-Nachversuch ({i}/{len(candidates)}): {mp4_path.name}")

        outcome = upload_manager._upload_to_youtube(
            mp4_path, data.get("title", mp4_path.stem), data.get("description", ""),
            data.get("hashtags"), publish=True,
        )
        if not outcome.success:
            failed += 1
            logger.warning(
                "YouTube-Nachversuch für '%s' erneut fehlgeschlagen: %s", mp4_path.name, outcome.detail,
            )
            continue

        ok += 1
        data["youtube_uploaded"] = True
        data["youtube_url"] = outcome.url
        try:
            atomic_io.atomic_write_json(sidecar_path, data)
        except OSError as e:
            logger.warning(
                "YouTube-Upload für '%s' erfolgreich (%s), aber Sidecar-Update fehlgeschlagen: %s",
                mp4_path.name, outcome.url, e,
            )
        logger.info("📺 YouTube-Nachversuch erfolgreich für '%s' -> %s", mp4_path.name, outcome.url)

    return ok, failed


def find_missing_instagram_uploads(uploaded_clips_dir: Path, streamer_name: Optional[str] = None) -> List[Path]:
    """Instagram's own equivalent of find_missing_youtube_uploads() above — found in a
    2026-08-22 upload-parity audit: unlike YouTube, Instagram had NO backlog-retry sweep at
    all. upload_clip_everywhere()'s own docstring already says only TikTok's outcome decides
    whether a clip moves into uploaded_clips/ — so a clip whose Instagram leg failed (a
    Playwright crash, an expired cookie, a selector-drift no-op that still counts as a
    non-confirmed attempt) or was still `pending`/unconfirmed at archive time had nothing that
    would ever revisit it once TikTok succeeded and moved it out of output/. This scans
    uploaded_clips/ for exactly that gap — clips whose sidecar shows `instagram_enabled: true`
    (this streamer had genuinely opted in, not e.g. an older clip from before the streamer
    turned it on) but `instagram_uploaded` isn't `true`.

    `streamer_name` filtering matches find_missing_youtube_uploads() exactly, same reasoning:
    uploaded_clips/ is a single flat directory shared by every concurrent streamer process.

    Returns sidecar-having .mp4 paths only, same reasoning as find_missing_youtube_uploads()."""
    if not uploaded_clips_dir.exists():
        return []

    missing: List[Path] = []
    for mp4_path in sorted(uploaded_clips_dir.glob("*.mp4")):
        sidecar_path = mp4_path.with_suffix(".json")
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        clip_owner = data.get("streamer_name")
        if streamer_name is not None and clip_owner is not None and clip_owner != streamer_name:
            continue
        if data.get("instagram_enabled") and not data.get("instagram_uploaded"):
            missing.append(mp4_path)
    return missing


def retry_missing_instagram_uploads(uploaded_clips_dir: Path, streamer_name: Optional[str] = None) -> Tuple[int, int]:
    """Retries ONLY the Instagram leg for clips found by find_missing_instagram_uploads() —
    never TikTok, same reasoning as retry_missing_youtube_uploads(). Uses
    upload_manager._upload_to_instagram() directly, with instagram_enabled=True (the sidecar
    filter above already confirmed this streamer opted in for this clip).

    A success that isn't yet `confirmed` (Instagram's own "clicked but no confirming signal
    seen" ambiguity — see InstagramOutcome's own docstring) is deliberately NOT marked
    instagram_uploaded here, same as upload_ledger.mark_unresolved()'s own reasoning: leaving
    it eligible for another retry next cycle is safer than guessing it went through.

    Returns (retried_ok, retried_failed), same semantics as retry_missing_youtube_uploads()."""
    candidates = find_missing_instagram_uploads(uploaded_clips_dir, streamer_name)[:INSTAGRAM_RETRY_BATCH_LIMIT]
    ok, failed = 0, 0

    for i, mp4_path in enumerate(candidates, start=1):
        sidecar_path = mp4_path.with_suffix(".json")
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Heartbeat (2026-08-24 dashboard-stall fix, same reasoning as run_deployment_phase's
        # own): _upload_to_instagram()'s internal retry-with-backoff can run 1-2+ minutes per
        # clip on its own.
        update_agent_state(current_action=f"📸 Instagram-Nachversuch ({i}/{len(candidates)}): {mp4_path.name}")

        outcome = upload_manager._upload_to_instagram(
            mp4_path, data.get("title", mp4_path.stem), data.get("description", ""),
            data.get("hashtags"), publish=True, instagram_enabled=True,
        )
        if not (outcome.success and outcome.confirmed):
            failed += 1
            logger.warning(
                "Instagram-Nachversuch für '%s' erneut fehlgeschlagen: %s", mp4_path.name, outcome.detail,
            )
            continue

        ok += 1
        data["instagram_uploaded"] = True
        try:
            atomic_io.atomic_write_json(sidecar_path, data)
        except OSError as e:
            logger.warning(
                "Instagram-Upload für '%s' erfolgreich, aber Sidecar-Update fehlgeschlagen: %s",
                mp4_path.name, e,
            )
        logger.info("📸 Instagram-Nachversuch erfolgreich für '%s'", mp4_path.name)

    return ok, failed


def purge_low_scoring_clips(
    batch: "train_loop.CriticBatch",
    rendered: Dict[str, Path],
    clips_path: Path,
    threshold: int,
) -> Tuple[int, int, List[Tuple[dict, Path, Optional[int]]]]:
    """Phase 3 (Purge): delete the .mp4 and remove the clips.json entry for every clip whose
    critic reward_score is below `threshold`. Clips the critic couldn't score (e.g. its
    response was unparseable) are kept rather than guessed at — never delete on ambiguity.

    Returns (kept, deleted, survivors) — survivors is [(clip, output_path, reward_score),
    ...] for every clip that made it through. Phase 5 (Deployment) uploads them and records
    reward_score alongside the AI's own metadata in each clip's uploaded_clips/ sidecar, for
    later correlation against real view/like counts in viral_memory.json."""
    score_by_title = {v.clip_title: v.reward_score for v in batch.verdicts}

    with open(clips_path, "r", encoding="utf-8") as f:
        clips_data = json.load(f)

    surviving_clips = []
    survivors: List[Tuple[dict, Path, Optional[int]]] = []
    kept, deleted = 0, 0

    for clip in clips_data.get("clips", []):
        title = clip.get("title", "")
        score = score_by_title.get(title)
        output_path = rendered.get(title)

        if score is not None and score < threshold:
            if output_path and output_path.exists():
                output_path.unlink()
                sidecar_path = output_path.with_suffix(".json")
                if sidecar_path.exists():
                    sidecar_path.unlink()
            logger.info("🗑️  Gelöscht (reward_score=%s < %d): '%s'", score, threshold, title)
            deleted += 1
        else:
            surviving_clips.append(clip)
            kept += 1
            if output_path and output_path.exists():
                survivors.append((clip, output_path, score))
                _persist_reward_score(output_path, score)

    atomic_io.atomic_write_json(clips_path, {"clips": surviving_clips})

    return kept, deleted, survivors


def should_deploy(auto_upload: bool, publish: bool, survivors: list) -> bool:
    """Whether Phase 5 (Deployment) should actually run this cycle. Both auto_upload AND
    publish are required, not just auto_upload — TikTok has no draft-save action anymore
    (confirmed 2026-08-18: an abandoned upload is discarded, not saved), so auto_upload
    without publish has nothing safe to do and must be a no-op, not a partial upload."""
    return bool(auto_upload and publish and survivors)


def run_deployment_phase(
    survivors: List[Tuple[dict, Path, Optional[int]]], publish: bool, instagram: bool = False,
    streamer_name: Optional[str] = None,
) -> Tuple[int, int]:
    """Phase 5 (Deployment): upload every clip that survived Phase 3's purge to every
    configured platform via upload_manager.py (2026-08-20: TikTok + YouTube Shorts, 2026-08-21:
    + Instagram Reels, was TikTok-only through tiktok_uploader.py directly), then move
    successfully uploaded files out of output/ into uploaded_clips/ to keep it clean. A failed
    upload just leaves that clip in output/ for a future opportunity — it never stops the
    loop. A clip counts as "uploaded" here based on TikTok's result alone (unchanged from
    before this file supported multiple platforms) — see the metadata sidecar for the full
    per-platform breakdown, including whether YouTube/Instagram succeeded independently.

    `instagram`, unlike `publish`, defaults False and is a genuinely separate opt-in — see
    upload_manager._upload_to_instagram()'s own docstring for why: this automation has never
    been run against a live Instagram session, so it stays off for every streamer until
    verified working and explicitly turned on in streamers.json (see orchestrator.py's
    build_auto_pilot_cmd()).

    `streamer_name`, when known (run_cycle()'s own streamer_handle), is recorded in the
    metadata sidecar so find_missing_youtube_uploads() can later scope its retry scan to only
    this streamer's own clips — uploaded_clips/ is a single flat directory shared by every
    concurrent streamer process (found in review, 2026-08-21: unlike output/<streamer>/, it
    was never namespaced), so without this every streamer's cycle was retrying the ENTIRE
    fleet's shared YouTube backlog, not just its own — N processes redundantly re-scanning and
    racing upload_ledger's pending-lock over the same clips every cycle.

    Only ever called with publish=True (see run_cycle()) — TikTok has no draft-save action
    anymore (confirmed 2026-08-18: an abandoned upload is discarded, not saved), so there is
    no safe reason to call this with publish=False; upload_manager.upload_clip_everywhere()
    would just no-op every clip on every platform and report every single one as "failed" for
    no real reason.

    Alongside each moved .mp4, writes a metadata sidecar .json (title, description,
    hashtags, the exact caption text posted, viral_score/energy_rating/reward_score, when
    it was uploaded) — this is the "clip metadata" metrics_tracker.py later matches against
    real TikTok view/like counts to build viral_memory.json."""
    UPLOADED_CLIPS_DIR.mkdir(exist_ok=True)
    uploaded, failed = 0, 0
    total = len(survivors)

    for i, (clip, output_path, reward_score) in enumerate(survivors, start=1):
        title = clip.get("title", output_path.name)
        # Heartbeat (2026-08-24 dashboard-stall fix): the caller only sets current_action once,
        # before this whole loop starts. A single clip's own upload can legitimately run
        # 1-4+ minutes (pacing waits on 2+ platforms, Instagram's own up to-3-attempt retry
        # backoff) — with several clips in one batch, agent_state's last_updated could go
        # stale past app.py's STALE_THRESHOLD_SECONDS (300s) while genuinely, correctly busy,
        # falsely flagging a healthy agent "offline" on the dashboard. update_agent_state()
        # merges into the existing state (state.update(updates)), so passing only
        # current_action here is safe — it never clobbers target_streamer/cycle counts/etc.
        # that the caller already set.
        update_agent_state(current_action=f"📤 Upload läuft ({i}/{total}): {title}")
        hashtags = clip.get("hashtags") or tiktok_uploader.DEFAULT_HASHTAGS
        description = clip.get("description") or title
        caption = tiktok_uploader.build_caption_text(description, hashtags)

        result = upload_manager.upload_clip_everywhere(
            output_path, title, description, hashtags, publish=publish, instagram_enabled=instagram,
        )
        outcome = result.tiktok
        if not outcome.success:
            failed += 1
            logger.warning("Upload fehlgeschlagen für '%s' — bleibt in output/", title)
            continue
        if not outcome.confirmed:
            # Treated the same as a failed upload (2026-08-18): a click that isn't confirmed
            # is exactly as unverified as one that raised — archiving it into uploaded_clips/
            # anyway used to leave phantom "confirmed": false entries that TikTok never
            # actually received, with no automatic retry (metrics_tracker only ever revisits
            # uploaded_clips/, never output/). Leaving it in output/ lets the next cycle try
            # again instead of silently treating an unverified click as done.
            #
            # This next cycle's retry re-calls upload_clip_everywhere() above — safe to repeat
            # for the exact same clip now (2026-08-21): every platform's own already-
            # succeeded/in-flight state is tracked centrally by upload_ledger.py (keyed by the
            # clip's content hash, not this output_path), and checked inside
            # upload_manager.py's _upload_to_tiktok()/_upload_to_youtube()/_upload_to_instagram()
            # themselves before any network call — so a retry here never re-uploads a platform
            # that already genuinely succeeded, no matter which of the three is still pending.
            # See upload_ledger.py's own module docstring for the incident this replaced a
            # narrower, per-clip-sidecar version of this same fix (commit 0aa31b1).
            failed += 1
            logger.warning(
                "Upload für '%s' wurde geklickt, aber nicht bestätigt (kein Redirect/Erfolgs-"
                "Toast beobachtet) — bleibt in output/ für einen erneuten Versuch.", title,
            )
            continue

        uploaded += 1
        try:
            destination = UPLOADED_CLIPS_DIR / output_path.name
            shutil.move(str(output_path), str(destination))

            # The render-time metadata sidecar (process._write_clip_metadata_sidecar) is left
            # behind in output/ once only the .mp4 is moved — clean it up rather than leaving
            # an orphaned .json with nothing to describe.
            render_sidecar = output_path.with_suffix(".json")
            if render_sidecar.exists():
                render_sidecar.unlink()

            metadata = {
                "title": title,
                "description": description,
                "hashtags": hashtags,
                "caption": caption,
                "viral_score": clip.get("viral_score"),
                "energy_rating": clip.get("energy_rating"),
                "reward_score": reward_score,
                # Performance-feedback attributes (2026-08-21, see optimization_engine.py) —
                # set by process.process_clips_iter() at render time and carried through
                # clips.json's own on-disk round-trip (see that function's comment) all the
                # way to here. None for clips rendered before this feature existed.
                "layout": clip.get("layout"),
                "music_track": clip.get("music_track"),
                "hook_style": clip.get("hook_style"),
                "title_style": clip.get("title_style"),
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                # Which streamer this clip came from — uploaded_clips/ is a single flat
                # directory shared by every concurrent streamer process (see this function's
                # own docstring); find_missing_youtube_uploads() uses this to scope its retry
                # scan to just this streamer's clips instead of the whole fleet's. None for a
                # manual/single-video run with no --streamer-name, or for clips archived before
                # this field existed (find_missing_youtube_uploads() treats a missing value as
                # "unowned" and still includes it, so nothing already in uploaded_clips/ is
                # orphaned by this change).
                "streamer_name": streamer_name,
                "publish": publish,
                "confirmed": outcome.confirmed,
                "youtube_uploaded": result.youtube.success,
                "youtube_url": result.youtube.url,
                # instagram_enabled records whether this streamer had Instagram turned on for
                # THIS clip (distinct from result.instagram.success) — metrics_tracker.py's
                # deletion guard needs to tell "Instagram wasn't applicable here" apart from
                # "Instagram was applicable and hasn't succeeded yet", the same distinction
                # `publish` already draws for the YouTube leg.
                "instagram_enabled": instagram,
                "instagram_uploaded": result.instagram.success and result.instagram.confirmed,
            }
            atomic_io.atomic_write_json(destination.with_suffix(".json"), metadata)

            youtube_note = result.youtube.url if result.youtube.success else (
                "übersprungen" if not result.youtube.attempted else "fehlgeschlagen"
            )
            logger.info(
                "📦 '%s' hochgeladen und nach %s verschoben (YouTube: %s)",
                title, destination, youtube_note,
            )
        except OSError as e:
            logger.warning("Upload für '%s' erfolgreich, aber Verschieben nach %s fehlgeschlagen: %s", title, UPLOADED_CLIPS_DIR, e)

    return uploaded, failed


def _cleanup_cycle_temp_files(video_path: Path, wav_path: Path, transcription_path: Path, clips_path: Path, live: bool) -> None:
    """Deletes this cycle's one-time-use temp artifacts once the cycle is fully done with
    them (called from a finally block, so this runs on every exit path — the early "no
    content found" return, a normal completed cycle, or the cycle raising) — the raw
    recording chunk, extracted audio, transcript, and clips selection. Rejected clips
    themselves are already cleaned up by purge_low_scoring_clips(); this is everything else
    "unused or temporary" the pipeline leaves behind (2026-08-19).

    Live mode ONLY: in non-live (--video/VOD) mode, video_path/wav_path/transcription_path
    are deliberately reused across cycles — ingest.extract_audio()/transcribe.transcribe()
    skip redoing the work if the file already exists (see L-05 in the audit). Deleting them
    here would silently break that caching and force a full re-extraction/re-transcription of
    the same static video every single cycle. In live mode, each cycle records a genuinely
    fresh .ts chunk with its own timestamped filename, so there is nothing for a next cycle
    to reuse."""
    if not live:
        return
    for path in (video_path, wav_path, transcription_path, clips_path):
        if path and path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Could not delete temp file %s: %s", path, e)


def run_cycle(
    video_path: Path,
    profile: Optional[dict],
    layout: str,
    video_format: str,
    highlight_color: str,
    purge_threshold: int,
    critic_model: str,
    cycle: int,
    target_streamer: str,
    kept_total: int,
    purged_total: int,
    uploaded_total: int,
    live: bool,
    auto_upload: bool,
    publish: bool,
    streamer_handle: Optional[str] = None,
    instagram: bool = False,
) -> Tuple[int, int, int]:
    """Runs one full Collect -> Evaluate -> Purge -> (optionally) Deploy cycle, updating
    agent_state.json at each phase transition. Returns (kept, deleted, uploaded) for THIS
    cycle, so the caller can track running totals across cycles.

    `streamer_handle`, when known, is a clean @-mentionable identity (streamers.json's
    `name`, or a profile's `name`) distinct from `target_streamer` — the latter can be a raw
    stream URL when neither --streamer-name nor --profile was given, which is not something
    the LLM should ever be told to @-mention in a caption."""
    common_state = dict(current_cycle=cycle, target_streamer=target_streamer)
    # Assigned before the try so the finally block's cleanup call is always well-defined, even
    # if analyze.analyze() itself is what raises (see the try's own docstring note below).
    wav_path = None
    transcription_path = None
    clips_path = None

    try:
        update_agent_state(
            current_action="🔴 Aufnahme läuft" if live else "📥 Quelle wird geladen & Audio extrahiert",
            **common_state,
        )
        wav_path = ingest.extract_audio(video_path)

        update_agent_state(current_action="📝 Transkription läuft (Whisper)", **common_state)
        transcription_path = transcribe.transcribe(wav_path)

        update_agent_state(current_action="🤖 KI-Analyse & Clip-Auswahl läuft", **common_state)
        # 2026-08-21: found live — this call used to sit BEFORE the try/finally below, so
        # when it raised (e.g. a missing/invalid GEMINI_API_KEY -> a non-retryable 401 from
        # llm_utils), the finally's _cleanup_cycle_temp_files() never ran at all: that cycle's
        # raw recording chunk, extracted audio, and transcript leaked to disk forever. Every
        # subsequent failed cycle (there's no limit on retries — see main()'s error-cooldown
        # loop) leaked another set, unbounded, for as long as the failure persisted.
        clips_path = analyze.analyze(
            transcription_path, audio_path=wav_path, profile=profile, streamer_name=streamer_handle,
        )

        with open(clips_path, "r", encoding="utf-8") as f:
            clips_data = json.load(f)

        # Phases 1-3 (Collect/Evaluate/Purge) only run when this cycle actually found new
        # clips worth rendering — kept/deleted/survivors default to "nothing new" otherwise.
        # Deliberately NOT an early return (2026-08-23 fix): this function used to return here
        # immediately on a "no clips" cycle, which also skipped Phase 5 below entirely —
        # including the output/ backlog sweep and the YouTube/Instagram retry sweeps, despite
        # their own docstrings promising they "run every cycle regardless of whether this
        # cycle had its own survivors". Found live: a content-quiet streamer (a stream with
        # long stretches of nothing clip-worthy happening) could have an already-rendered
        # backlog clip, or a clip stuck mid-upload on one platform, sit stuck for a FULL DAY —
        # upload_ledger.json showed several "pending" entries 14-37 hours old, all belonging to
        # streamers whose recent cycles kept coming back with zero new clips, so the retry
        # sweep that would have released them (upload_ledger.try_mark_pending()'s own staleness
        # check) never got a chance to run at all.
        kept, deleted, survivors = 0, 0, []
        rendered: Dict[str, Path] = {}
        if not clips_data.get("clips"):
            logger.info(
                "Zyklus: kein Content mit hohem viralem Potenzial gefunden, nichts gerendert — "
                "Backlog/Nachversuche laufen unten trotzdem weiter."
            )
            update_agent_state(current_action="😴 Kein Content gefunden — prüfe Backlog/Nachversuche", **common_state)
        else:
            batch_size = random.randint(BATCH_SIZE_MIN, BATCH_SIZE_MAX)
            batch_clips = _trim_to_batch(clips_path, batch_size)
            logger.info("Phase 1 (Collect): %d Clip(s) für diesen Zyklus ausgewählt", len(batch_clips))

            update_agent_state(current_action=f"🎬 Rendering läuft ({len(batch_clips)} Clip(s))", **common_state)
            transcript = analyze.load_transcript(transcription_path)
            rendered: Dict[str, Path] = {}
            for i, total, clip, output_path in process_module.process_clips_iter(
                video_path, layout=layout, video_format=video_format,
                highlight_color=highlight_color, transcript=transcript,
                output_dir=_output_dir_override, streamer_name=streamer_handle,
            ):
                rendered[clip["title"]] = output_path

            # Phase 2 (Evaluate & Update): score the batch (narrative + visual composition, via
            # preview frames extracted from the just-rendered clips) and fold new content/visual/
            # viral-pattern rules into ai_guidelines.txt — reused as-is from train_loop.py.
            update_agent_state(current_action="🧠 KI bewertet die Clips (Critic)", **common_state)
            guidelines_path, batch = train_loop.run_training_loop(
                clips_path=clips_path, model=critic_model, rendered=rendered
            )

            # Phase 3 (Clean & Purge)
            update_agent_state(current_action="🧹 Bereinigung — lösche schwache Clips", **common_state)
            kept, deleted, survivors = purge_low_scoring_clips(batch, rendered, clips_path, purge_threshold)

            logger.info(
                "✅ Batch abgeschlossen: %d Clip(s) behalten, %d gelöscht, Regeln aktualisiert (%s).",
                kept, deleted, guidelines_path,
            )

        # Phase 5 (Deployment) — every clip that survived the purge gets uploaded to TikTok.
        # Requires publish too, not just auto_upload: TikTok has no draft-save action anymore
        # (confirmed 2026-08-18 — an abandoned upload is discarded, not saved), so there is no
        # safe partial deployment to perform without an explicit intent to actually go live.
        uploaded = 0
        output_dir = _output_dir_override or process_module.OUTPUT_DIR
        if auto_upload and not publish:
            # Runs every cycle for this configuration, not just ones with new survivors —
            # clips from PAST cycles need to age out too, not only ones just rendered (see
            # OUTPUT_RETENTION_DAYS's own comment for why this exists).
            purge_old_local_only_clips(output_dir)
            if survivors:
                logger.info(
                    "⏭️ Deployment übersprungen: --auto-upload ist an, aber --publish nicht — TikTok "
                    "hat keinen Entwurfs-Modus mehr, es gibt also nichts Sicheres zu tun. %d Clip(s) "
                    "bleiben in output/ zur manuellen Durchsicht (bis zu %d Tage).",
                    len(survivors), OUTPUT_RETENTION_DAYS,
                )
        elif auto_upload and publish:
            # Backlog reconciliation: fold in any already-evaluated clip a PAST cycle left
            # behind in output/ (crashed process, VPS restart, a prior upload attempt that
            # failed and outlived its cycle) — see find_backlog_clips()'s own docstring for why
            # this configuration otherwise leaks silently. Deliberately never runs for
            # publish=False streamers above — those clips are intentionally local-only, not
            # backlog (see purge_old_local_only_clips instead).
            backlog = find_backlog_clips(output_dir, exclude=set(rendered.values()))[:BACKLOG_BATCH_LIMIT]
            if backlog:
                logger.info(
                    "📦 %d Backlog-Clip(s) aus früheren Zyklen gefunden (bereits bewertet, nie "
                    "hochgeladen) — werden in diesem Zyklus mit versucht.", len(backlog),
                )
            deploy_batch = survivors + backlog
            if should_deploy(auto_upload, publish, deploy_batch):
                update_agent_state(current_action=f"📤 Upload läuft ({len(deploy_batch)} Clip(s))", **common_state)
                uploaded, upload_failed = run_deployment_phase(
                    deploy_batch, publish, instagram, streamer_name=streamer_handle,
                )
                logger.info("📤 Deployment: %d hochgeladen, %d fehlgeschlagen", uploaded, upload_failed)

            # YouTube-only backlog: a clip already live on TikTok (hence already archived into
            # uploaded_clips/) whose YouTube leg failed or was never attempted — see
            # find_missing_youtube_uploads()'s own docstring. Runs every cycle regardless of
            # whether this cycle had its own survivors, same reasoning as the output/ backlog
            # scan above; never touches TikTok.
            yt_ok, yt_failed = retry_missing_youtube_uploads(UPLOADED_CLIPS_DIR, streamer_name=streamer_handle)
            if yt_ok or yt_failed:
                logger.info("📺 YouTube-Nachversuch: %d erfolgreich, %d fehlgeschlagen", yt_ok, yt_failed)

            # Instagram-only backlog: same gap, same fix, as the YouTube retry directly above —
            # see find_missing_instagram_uploads()'s own docstring (2026-08-22 upload-parity
            # audit finding: this side had no retry sweep at all until now).
            ig_ok, ig_failed = retry_missing_instagram_uploads(UPLOADED_CLIPS_DIR, streamer_name=streamer_handle)
            if ig_ok or ig_failed:
                logger.info("📸 Instagram-Nachversuch: %d erfolgreich, %d fehlgeschlagen", ig_ok, ig_failed)

        update_agent_state(
            current_action="✅ Zyklus abgeschlossen — Cooldown",
            clips_kept_total=kept_total + kept, clips_purged_total=purged_total + deleted,
            clips_uploaded_total=uploaded_total + uploaded,
            **common_state,
        )
        return kept, deleted, uploaded
    finally:
        # Aggressive cleanup (2026-08-19): the raw .ts chunk, extracted .wav, and this
        # cycle's transcript/clips-selection temp files are all one-time-use in live mode —
        # runs on every exit path (early return, normal return, or an exception propagating
        # up to main()'s error-cooldown handler), not just the happy path.
        _cleanup_cycle_temp_files(video_path, wav_path, transcription_path, clips_path, live)


def resolve_static_video(args, url: Optional[str]) -> Path:
    """Resolved once and reused for every cycle in non-live mode (a VOD or local file
    doesn't change between cycles — only the learned guidelines applied to it do)."""
    if args.video:
        return Path(args.video)
    if url:
        return ingest.download_from_url(url)
    return process_module.find_source_video(None)


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous self-improving clipping agent: collect, evaluate, purge, repeat."
    )
    parser.add_argument("--video", type=Path, default=None, help="Path to a local video to reprocess every cycle")
    parser.add_argument("--url", default=None, help="VOD URL (downloaded once) or livestream URL (with --live)")
    parser.add_argument("--profile", default=None, help="Streamer profile name (profiles/<name>.json)")
    parser.add_argument(
        "--live", action="store_true",
        help="Record a fresh stream chunk every cycle instead of reprocessing one static video",
    )
    parser.add_argument(
        "--chunk-duration", type=int, default=stream_watcher.DEFAULT_CHUNK_DURATION,
        help="Seconds per recorded chunk in --live mode",
    )
    parser.add_argument("--layout", choices=process_module.SELECTABLE_LAYOUTS, default=process_module.LAYOUT_AUTO)
    parser.add_argument("--format", dest="video_format", choices=tuple(process_module.VIDEO_FORMATS), default=process_module.DEFAULT_FORMAT)
    parser.add_argument("--highlight-color", default=process_module.DEFAULT_HIGHLIGHT_COLOR)
    parser.add_argument(
        "--purge-threshold", type=int, default=DEFAULT_PURGE_THRESHOLD,
        help="Clips with reward_score below this are deleted (default: %(default)s)",
    )
    parser.add_argument("--critic-model", default=train_loop.MODEL, help="Critic model name")
    parser.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_SECONDS, help="Seconds to wait between successful cycles")
    parser.add_argument(
        "--error-cooldown", type=int, default=DEFAULT_ERROR_COOLDOWN_SECONDS,
        help="Seconds to wait before retrying after a failed cycle (network error, stream drop, etc.)",
    )
    parser.add_argument("--max-cycles", type=int, default=None, help="Stop after N cycles (omit to run forever)")
    parser.add_argument(
        "--auto-upload", action="store_true",
        help="Enable Phase 5 (Deployment). Requires --publish too — TikTok has no draft-save "
        "action anymore, so --auto-upload alone does nothing (see README_UPLOAD.md)",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="With --auto-upload: actually post survivors live. Required for Phase 5 to do "
        "anything at all — there is no safer partial-upload mode anymore",
    )
    parser.add_argument(
        "--instagram", action="store_true",
        help="With --auto-upload and --publish: also post survivors to Instagram Reels via "
        "upload_instagram_playwright.py. Separate opt-in from --publish, defaulting off — "
        "this automation has never been verified against a live Instagram session (see that "
        "module's own docstring); only turn this on after a manual --headed test succeeds",
    )
    parser.add_argument(
        "--streamer-name", default=None,
        help="Set by orchestrator.py when running as one of several concurrent streamer "
        "subprocesses — namespaces this process's agent_state file, rendered-clip output "
        "directory, and recording chunk filenames so two streamers running at once can't "
        "collide on any of the three. Omit for a manual/standalone run (original shared "
        "agent_state.json / output/ behavior).",
    )
    args = parser.parse_args()

    if args.publish and not args.auto_upload:
        parser.error("--publish requires --auto-upload")
    if args.instagram and not args.publish:
        parser.error("--instagram requires --publish")
    if args.auto_upload and not args.publish:
        logger.warning(
            "--auto-upload was given without --publish — Phase 5 (Deployment) will be "
            "skipped every cycle. TikTok has no draft-save action anymore (an abandoned "
            "upload is discarded, not saved), so there is nothing safe for --auto-upload to "
            "do without --publish. Survivors will stay in output/ for manual review."
        )

    profile_dict = None
    if args.profile:
        profile_dict = profiles.load_profile_or_fallback(args.profile).model_dump()

    url = args.url or (profile_dict["stream_url"] if profile_dict else None)
    if args.live and not url:
        parser.error("--live requires --url or a --profile with a stream_url set")

    target_streamer = (
        profile_dict["name"] if profile_dict
        else (url or (str(args.video) if args.video else "lokales Video"))
    )

    streamer_slug: Optional[str] = None
    if args.streamer_name:
        global _agent_state_path_override, _output_dir_override
        streamer_slug = process_module.slugify(args.streamer_name)
        _agent_state_path_override = Path(f"agent_state_{streamer_slug}.json")
        _output_dir_override = process_module.OUTPUT_DIR / streamer_slug
        logger.info(
            "Namespaced for concurrent multi-streamer operation: agent_state=%s, output_dir=%s",
            _agent_state_path_override, _output_dir_override,
        )

    # A clean @-mentionable identity, distinct from target_streamer above (which can be a
    # raw stream URL) — args.streamer_name (from orchestrator.py) takes priority since it's
    # the actual streamers.json name; a --profile's name is the fallback for a manual run.
    streamer_handle: Optional[str] = args.streamer_name or (profile_dict["name"] if profile_dict else None)

    cached_video_path: Optional[Path] = None
    kept_total, purged_total, uploaded_total = 0, 0, 0

    logger.info(
        "🤖 Auto-Pilot startet (live=%s, purge_threshold=%d, batch=%d-%d Clips/Zyklus, auto_upload=%s)",
        args.live, args.purge_threshold, BATCH_SIZE_MIN, BATCH_SIZE_MAX, args.auto_upload,
    )
    update_agent_state(
        current_action="🚀 Auto-Pilot gestartet", current_cycle=0, target_streamer=target_streamer,
        clips_kept_total=0, clips_purged_total=0, clips_uploaded_total=0,
        purge_threshold=args.purge_threshold, live=args.live, auto_upload=args.auto_upload,
    )

    cycle = 0
    try:
        while args.max_cycles is None or cycle < args.max_cycles:
            cycle += 1
            logger.info("=== Zyklus %d ===", cycle)

            try:
                if args.live:
                    update_agent_state(
                        current_action="🔴 Aufnahme läuft", current_cycle=cycle, target_streamer=target_streamer,
                    )
                    video_path = stream_watcher.record_stream_chunk(url, args.chunk_duration, streamer_slug)
                else:
                    if cached_video_path is None:
                        cached_video_path = resolve_static_video(args, url)
                    video_path = cached_video_path

                kept, deleted, uploaded = run_cycle(
                    video_path, profile_dict, args.layout, args.video_format,
                    args.highlight_color, args.purge_threshold, args.critic_model,
                    cycle, target_streamer, kept_total, purged_total, uploaded_total,
                    args.live, args.auto_upload, args.publish, streamer_handle, args.instagram,
                )
                kept_total += kept
                purged_total += deleted
                uploaded_total += uploaded
            except Exception as e:
                logger.error(
                    "Zyklus %d fehlgeschlagen (%s: %s) — warte %ds und versuche es erneut.",
                    cycle, type(e).__name__, e, args.error_cooldown,
                )
                update_agent_state(
                    current_action=f"⚠️ Fehler: {e} — Cooldown ({args.error_cooldown}s)",
                    current_cycle=cycle, target_streamer=target_streamer,
                    clips_kept_total=kept_total, clips_purged_total=purged_total,
                    clips_uploaded_total=uploaded_total,
                )
                time.sleep(args.error_cooldown)
                continue

            if args.cooldown > 0:
                time.sleep(args.cooldown)
    except KeyboardInterrupt:
        logger.info("Auto-Pilot durch Nutzer gestoppt nach %d Zyklus/Zyklen.", cycle)
        update_agent_state(
            current_action="⏹️ Gestoppt (Nutzer)", current_cycle=cycle, target_streamer=target_streamer,
            clips_kept_total=kept_total, clips_purged_total=purged_total,
            clips_uploaded_total=uploaded_total,
        )


if __name__ == "__main__":
    main()
