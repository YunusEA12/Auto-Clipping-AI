"""Autonomous, self-improving agent loop: Collect -> Evaluate -> Purge -> Repeat, forever.

Each cycle:
  1. Collect  — run the existing ingest -> transcribe -> analyze -> process pipeline to
     render a fresh batch of 3-5 clips into output/.
  2. Evaluate — hand that batch to train_loop.py's critic, which scores each clip
     (reward_score -10..+10) and derives new AVOID/DO rules into ai_guidelines.txt.
  3. Purge    — physically delete any clip (its .mp4 AND its entry in the batch's
     *_clips.json) whose reward_score falls below --purge-threshold. Only "winners" survive
     on disk.
  4. Repeat   — go back to step 1. analyze.py re-reads ai_guidelines.txt fresh on every call
     (see analyze.load_ai_guidelines_section()), so each new cycle's clip selection is
     informed by everything the critic has learned so far.

This script does not duplicate any pipeline logic — it only orchestrates the existing
ingest.py / transcribe.py / analyze.py / process.py / train_loop.py / stream_watcher.py
functions in a loop.

Usage:
    python auto_pilot.py --video some_vod.mp4                    # reprocess one local VOD repeatedly
    python auto_pilot.py --url https://youtube.com/watch?v=...    # download once, reprocess repeatedly
    python auto_pilot.py --live --url https://twitch.tv/<channel> # record a fresh chunk every cycle
    python auto_pilot.py --profile eliasn97 --live                # same, via a streamer profile
"""

import argparse
import json
import logging
import random
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import analyze
import ingest
import process as process_module
import profiles
import stream_watcher
import train_loop
import transcribe

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE_MIN = 3
BATCH_SIZE_MAX = 5

DEFAULT_PURGE_THRESHOLD = 0
DEFAULT_COOLDOWN_SECONDS = 30
DEFAULT_ERROR_COOLDOWN_SECONDS = 90


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

    with open(clips_path, "w", encoding="utf-8") as f:
        json.dump({"clips": batch}, f, ensure_ascii=False, indent=2)

    return batch


def run_collection_phase(
    video_path: Path,
    profile: Optional[dict],
    layout: str,
    video_format: str,
    highlight_color: str,
) -> Tuple[Path, Dict[str, Path]]:
    """Phase 1 (Collect): ingest -> transcribe -> analyze -> trim to a small batch -> render.
    Returns (clips_path, {clip_title: rendered_output_path}) — the dict is empty if the
    quality gate in analyze.py found nothing worth clipping this cycle (a valid outcome,
    not a failure)."""
    wav_path = ingest.extract_audio(video_path)
    transcription_path = transcribe.transcribe(wav_path)
    clips_path = analyze.analyze(transcription_path, audio_path=wav_path, profile=profile)

    with open(clips_path, "r", encoding="utf-8") as f:
        clips_data = json.load(f)
    if not clips_data.get("clips"):
        return clips_path, {}

    batch_size = random.randint(BATCH_SIZE_MIN, BATCH_SIZE_MAX)
    batch = _trim_to_batch(clips_path, batch_size)
    logger.info("Phase 1 (Collect): %d Clip(s) für diesen Zyklus ausgewählt", len(batch))

    transcript = analyze.load_transcript(transcription_path)
    rendered: Dict[str, Path] = {}
    for i, total, clip, output_path in process_module.process_clips_iter(
        video_path, layout=layout, video_format=video_format,
        highlight_color=highlight_color, transcript=transcript,
    ):
        rendered[clip["title"]] = output_path

    return clips_path, rendered


def purge_low_scoring_clips(
    batch: "train_loop.CriticBatch",
    rendered: Dict[str, Path],
    clips_path: Path,
    threshold: int,
) -> Tuple[int, int]:
    """Phase 3 (Purge): delete the .mp4 and remove the clips.json entry for every clip whose
    critic reward_score is below `threshold`. Clips the critic couldn't score (e.g. its
    response was unparseable) are kept rather than guessed at — never delete on ambiguity."""
    score_by_title = {v.clip_title: v.reward_score for v in batch.verdicts}

    with open(clips_path, "r", encoding="utf-8") as f:
        clips_data = json.load(f)

    surviving_clips = []
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

    with open(clips_path, "w", encoding="utf-8") as f:
        json.dump({"clips": surviving_clips}, f, ensure_ascii=False, indent=2)

    return kept, deleted


def run_cycle(
    video_path: Path,
    profile: Optional[dict],
    layout: str,
    video_format: str,
    highlight_color: str,
    purge_threshold: int,
    critic_model: str,
) -> None:
    clips_path, rendered = run_collection_phase(video_path, profile, layout, video_format, highlight_color)

    if not rendered:
        logger.info("Zyklus abgeschlossen: kein Content mit hohem viralem Potenzial gefunden, nichts gerendert.")
        return

    # Phase 2 (Evaluate & Update): score the batch and fold new AVOID/DO rules into
    # ai_guidelines.txt — reused as-is from train_loop.py, no duplicated critic logic here.
    guidelines_path, batch = train_loop.run_training_loop(clips_path=clips_path, model=critic_model)

    # Phase 3 (Clean & Purge)
    kept, deleted = purge_low_scoring_clips(batch, rendered, clips_path, purge_threshold)

    logger.info(
        "✅ Batch abgeschlossen: %d Clip(s) behalten, %d gelöscht, Regeln aktualisiert (%s).",
        kept, deleted, guidelines_path,
    )


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
    args = parser.parse_args()

    profile_dict = None
    if args.profile:
        profile_dict = profiles.load_profile_or_fallback(args.profile).model_dump()

    url = args.url or (profile_dict["stream_url"] if profile_dict else None)
    if args.live and not url:
        parser.error("--live requires --url or a --profile with a stream_url set")

    cached_video_path: Optional[Path] = None

    logger.info(
        "🤖 Auto-Pilot startet (live=%s, purge_threshold=%d, batch=%d-%d Clips/Zyklus)",
        args.live, args.purge_threshold, BATCH_SIZE_MIN, BATCH_SIZE_MAX,
    )

    cycle = 0
    try:
        while args.max_cycles is None or cycle < args.max_cycles:
            cycle += 1
            logger.info("=== Zyklus %d ===", cycle)

            try:
                if args.live:
                    video_path = stream_watcher.record_stream_chunk(url, args.chunk_duration)
                else:
                    if cached_video_path is None:
                        cached_video_path = resolve_static_video(args, url)
                    video_path = cached_video_path

                run_cycle(
                    video_path, profile_dict, args.layout, args.video_format,
                    args.highlight_color, args.purge_threshold, args.critic_model,
                )
            except Exception as e:
                logger.error(
                    "Zyklus %d fehlgeschlagen (%s: %s) — warte %ds und versuche es erneut.",
                    cycle, type(e).__name__, e, args.error_cooldown,
                )
                time.sleep(args.error_cooldown)
                continue

            if args.cooldown > 0:
                time.sleep(args.cooldown)
    except KeyboardInterrupt:
        logger.info("Auto-Pilot durch Nutzer gestoppt nach %d Zyklus/Zyklen.", cycle)


if __name__ == "__main__":
    main()
