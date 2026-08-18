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

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

BATCH_SIZE_MIN = 3
BATCH_SIZE_MAX = 5

DEFAULT_PURGE_THRESHOLD = 0
DEFAULT_COOLDOWN_SECONDS = 30
DEFAULT_ERROR_COOLDOWN_SECONDS = 90

# Where successfully-uploaded clips are archived to, out of output/ — keeps the working
# directory limited to clips still awaiting a decision (upload or the next purge pass).
UPLOADED_CLIPS_DIR = Path("uploaded_clips")

# Telemetry file the Streamlit "Agent Control Center" dashboard (app.py) polls to show what
# this process is doing right now — there's no direct connection between the two processes,
# just this JSON file on disk.
AGENT_STATE_PATH = Path("agent_state.json")


def update_agent_state(**updates) -> dict:
    """Merge `updates` into agent_state.json and write it back. Called at every phase
    transition in the loop below so app.py's Live Radar tab always reflects the agent's
    current phase, not just its state at the end of a cycle."""
    state = {}
    if AGENT_STATE_PATH.exists():
        try:
            state = json.loads(AGENT_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}

    state.update(updates)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    try:
        atomic_io.atomic_write_json(AGENT_STATE_PATH, state)
    except OSError as e:
        logger.warning("Could not write %s: %s", AGENT_STATE_PATH, e)

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
            logger.info("🗑️  Gelöscht (reward_score=%s < %d): '%s'", score, threshold, title)
            deleted += 1
        else:
            surviving_clips.append(clip)
            kept += 1
            if output_path and output_path.exists():
                survivors.append((clip, output_path, score))

    atomic_io.atomic_write_json(clips_path, {"clips": surviving_clips})

    return kept, deleted, survivors


def should_deploy(auto_upload: bool, publish: bool, survivors: list) -> bool:
    """Whether Phase 5 (Deployment) should actually run this cycle. Both auto_upload AND
    publish are required, not just auto_upload — TikTok has no draft-save action anymore
    (confirmed 2026-08-18: an abandoned upload is discarded, not saved), so auto_upload
    without publish has nothing safe to do and must be a no-op, not a partial upload."""
    return bool(auto_upload and publish and survivors)


def run_deployment_phase(survivors: List[Tuple[dict, Path, Optional[int]]], publish: bool) -> Tuple[int, int]:
    """Phase 5 (Deployment): upload every clip that survived Phase 3's purge to TikTok via
    tiktok_uploader.py (cookie-authenticated Playwright bot), then move successfully
    uploaded files out of output/ into uploaded_clips/ to keep it clean. A failed upload
    just leaves that clip in output/ for a future opportunity — it never stops the loop.

    Only ever called with publish=True (see run_cycle()) — TikTok has no draft-save action
    anymore (confirmed 2026-08-18: an abandoned upload is discarded, not saved), so there is
    no safe reason to call this with publish=False; tiktok_uploader.try_upload_clip() would
    just no-op every clip and report every single one as "failed" for no real reason.

    Alongside each moved .mp4, writes a metadata sidecar .json (title, description,
    hashtags, the exact caption text posted, viral_score/energy_rating/reward_score, when
    it was uploaded) — this is the "clip metadata" metrics_tracker.py later matches against
    real TikTok view/like counts to build viral_memory.json."""
    UPLOADED_CLIPS_DIR.mkdir(exist_ok=True)
    uploaded, failed = 0, 0

    for clip, output_path, reward_score in survivors:
        title = clip.get("title", output_path.name)
        hashtags = clip.get("hashtags") or tiktok_uploader.DEFAULT_HASHTAGS
        description = clip.get("description") or title
        caption = tiktok_uploader.build_caption_text(description, hashtags)

        outcome = tiktok_uploader.try_upload_clip(output_path, description, hashtags, publish=publish)
        if not outcome.success:
            failed += 1
            logger.warning("Upload fehlgeschlagen für '%s' — bleibt in output/", title)
            continue
        if not outcome.confirmed:
            logger.warning(
                "Upload für '%s' wurde geklickt, aber nicht bestätigt (kein Redirect/Formular-"
                "Abbau beobachtet) — wird trotzdem als hochgeladen verbucht.", title,
            )

        uploaded += 1
        try:
            destination = UPLOADED_CLIPS_DIR / output_path.name
            shutil.move(str(output_path), str(destination))

            metadata = {
                "title": title,
                "description": description,
                "hashtags": hashtags,
                "caption": caption,
                "viral_score": clip.get("viral_score"),
                "energy_rating": clip.get("energy_rating"),
                "reward_score": reward_score,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "publish": publish,
                "confirmed": outcome.confirmed,
            }
            atomic_io.atomic_write_json(destination.with_suffix(".json"), metadata)

            logger.info("📦 '%s' hochgeladen und nach %s verschoben", title, destination)
        except OSError as e:
            logger.warning("Upload für '%s' erfolgreich, aber Verschieben nach %s fehlgeschlagen: %s", title, UPLOADED_CLIPS_DIR, e)

    return uploaded, failed


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
) -> Tuple[int, int, int]:
    """Runs one full Collect -> Evaluate -> Purge -> (optionally) Deploy cycle, updating
    agent_state.json at each phase transition. Returns (kept, deleted, uploaded) for THIS
    cycle, so the caller can track running totals across cycles."""
    common_state = dict(current_cycle=cycle, target_streamer=target_streamer)

    update_agent_state(
        current_action="🔴 Aufnahme läuft" if live else "📥 Quelle wird geladen & Audio extrahiert",
        **common_state,
    )
    wav_path = ingest.extract_audio(video_path)

    update_agent_state(current_action="📝 Transkription läuft (Whisper)", **common_state)
    transcription_path = transcribe.transcribe(wav_path)

    update_agent_state(current_action="🤖 KI-Analyse & Clip-Auswahl läuft", **common_state)
    clips_path = analyze.analyze(transcription_path, audio_path=wav_path, profile=profile)

    with open(clips_path, "r", encoding="utf-8") as f:
        clips_data = json.load(f)
    if not clips_data.get("clips"):
        logger.info("Zyklus abgeschlossen: kein Content mit hohem viralem Potenzial gefunden, nichts gerendert.")
        update_agent_state(
            current_action="😴 Kein Content gefunden — warte auf nächsten Zyklus",
            clips_kept_total=kept_total, clips_purged_total=purged_total,
            clips_uploaded_total=uploaded_total, **common_state,
        )
        return 0, 0, 0

    batch_size = random.randint(BATCH_SIZE_MIN, BATCH_SIZE_MAX)
    batch_clips = _trim_to_batch(clips_path, batch_size)
    logger.info("Phase 1 (Collect): %d Clip(s) für diesen Zyklus ausgewählt", len(batch_clips))

    update_agent_state(current_action=f"🎬 Rendering läuft ({len(batch_clips)} Clip(s))", **common_state)
    transcript = analyze.load_transcript(transcription_path)
    rendered: Dict[str, Path] = {}
    for i, total, clip, output_path in process_module.process_clips_iter(
        video_path, layout=layout, video_format=video_format,
        highlight_color=highlight_color, transcript=transcript,
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
    if auto_upload and not publish and survivors:
        logger.info(
            "⏭️ Deployment übersprungen: --auto-upload ist an, aber --publish nicht — TikTok "
            "hat keinen Entwurfs-Modus mehr, es gibt also nichts Sicheres zu tun. %d Clip(s) "
            "bleiben in output/ zur manuellen Durchsicht.", len(survivors),
        )
    elif should_deploy(auto_upload, publish, survivors):
        update_agent_state(current_action=f"📤 Upload läuft ({len(survivors)} Clip(s))", **common_state)
        uploaded, upload_failed = run_deployment_phase(survivors, publish)
        logger.info("📤 Deployment: %d hochgeladen, %d fehlgeschlagen", uploaded, upload_failed)

    update_agent_state(
        current_action="✅ Zyklus abgeschlossen — Cooldown",
        clips_kept_total=kept_total + kept, clips_purged_total=purged_total + deleted,
        clips_uploaded_total=uploaded_total + uploaded,
        **common_state,
    )
    return kept, deleted, uploaded


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
    args = parser.parse_args()

    if args.publish and not args.auto_upload:
        parser.error("--publish requires --auto-upload")
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
                    video_path = stream_watcher.record_stream_chunk(url, args.chunk_duration)
                else:
                    if cached_video_path is None:
                        cached_video_path = resolve_static_video(args, url)
                    video_path = cached_video_path

                kept, deleted, uploaded = run_cycle(
                    video_path, profile_dict, args.layout, args.video_format,
                    args.highlight_color, args.purge_threshold, args.critic_model,
                    cycle, target_streamer, kept_total, purged_total, uploaded_total,
                    args.live, args.auto_upload, args.publish,
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
