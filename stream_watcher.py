"""Live-stream watcher prototype: chunk a livestream via streamlink, transcribe + analyze
each chunk in the background while the next one records, and optionally auto-render any
high-energy clips found.

Runs standalone from the terminal, independent of the Streamlit UI:
    python stream_watcher.py --url https://twitch.tv/<channel> --auto-render
"""

import argparse
import concurrent.futures
import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

import analyze
import ingest
import notify
import process as process_module
import profiles
import tiktok_uploader
import transcribe

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
HOT_CLIPS_LOG_PATH = TEMP_DIR / "live_hot_clips.jsonl"

DEFAULT_CHUNK_DURATION = 180  # seconds (3 minutes)
DEFAULT_MAX_WORKERS = 2
RETRY_DELAY_SECONDS = 15
ENERGY_THRESHOLD = 7  # clip.energy_rating >= this counts as a "hot clip" worth flagging

# transcribe.py/analyze.py/process.py read and write fixed paths (temp/transcription.json,
# temp/clips.json) rather than per-call files. Two chunks processed on separate threads at
# the same time would clobber each other's intermediate JSON. This lock serializes the
# actual processing (transcribe -> analyze -> optional render) so only RECORDING the next
# chunk ever overlaps with processing the previous one — which is the actual bottleneck
# this feature is meant to remove (recording is real-time; processing is not).
_processing_lock = threading.Lock()


def resolve_streamlink_path() -> str:
    """Find the streamlink executable even when the venv isn't "activated" (no venv/Scripts
    on PATH) — the common case when this project is run as `venv\\Scripts\\python.exe ...`.
    pip installs console scripts next to the interpreter, so check there too. Public
    (no leading underscore) since orchestrator.py also needs it for its liveness checks."""
    on_path = shutil.which("streamlink")
    if on_path:
        return on_path

    script_name = "streamlink.exe" if sys.platform == "win32" else "streamlink"
    beside_python = Path(sys.executable).parent / script_name
    if beside_python.exists():
        return str(beside_python)

    raise RuntimeError(
        "streamlink executable not found (not on PATH, not next to the current Python "
        "interpreter). Install it with `pip install streamlink`."
    )


def record_stream_chunk(url: str, duration: int = DEFAULT_CHUNK_DURATION) -> Path:
    """Record `duration` seconds of the live stream (video+audio) via streamlink -> ffmpeg,
    saved as temp/live_chunk_[TIMESTAMP].ts.

    .ts (MPEG transport stream) instead of .wav: rendering a found clip later needs the
    actual video, not just audio, so the full A/V stream is captured with a fast `-c copy`
    remux (no re-encoding) straight off the live pipe. .ts also tolerates being cut off
    mid-stream far better than most container formats. Audio for Whisper is extracted from
    this file afterwards via ingest.extract_audio() — no separate audio-only capture needed.
    """
    TEMP_DIR.mkdir(exist_ok=True)
    timestamp = int(time.time())
    output_path = TEMP_DIR / f"live_chunk_{timestamp}.ts"

    streamlink_cmd = [resolve_streamlink_path(), url, "best", "--stdout", "--quiet"]
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", "pipe:0",
        "-t", str(duration),
        "-c", "copy",
        "-f", "mpegts",
        str(output_path),
    ]

    logger.info("Recording %ds chunk from %s -> %s", duration, url, output_path)
    # streamlink's own stdout must stay BINARY (it's the raw A/V stream piped straight into
    # ffmpeg's stdin) — do not add text=True/encoding here, that would corrupt the media data.
    streamlink_proc = subprocess.Popen(
        streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    try:
        # ffmpeg's own stderr log IS text, and must be decoded explicitly as UTF-8: without
        # it, text mode falls back to the system locale (cp1252/"charmap" on German Windows),
        # which raises UnicodeDecodeError the moment the log contains a German umlaut.
        ffmpeg_result = subprocess.run(
            ffmpeg_cmd,
            stdin=streamlink_proc.stdout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=duration + 60,
        )
    finally:
        if streamlink_proc.stdout:
            streamlink_proc.stdout.close()
        try:
            streamlink_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            streamlink_proc.kill()

    if ffmpeg_result.returncode != 0:
        stderr_tail = ffmpeg_result.stderr[-2000:] if ffmpeg_result.stderr else ""
        raise RuntimeError(f"ffmpeg failed while recording chunk: {stderr_tail}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Recording produced empty output: {output_path}")

    logger.info(
        "Chunk recorded: %s (%.1f MB)", output_path, output_path.stat().st_size / 1_000_000
    )
    return output_path


def log_hot_clip(chunk_path: Path, clip: dict) -> None:
    entry = {"chunk": chunk_path.name, **clip}
    with open(HOT_CLIPS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _cleanup(path: Optional[Path]) -> None:
    if path and path.exists():
        try:
            path.unlink()
            logger.info("Cleaned up %s", path)
        except OSError as e:
            logger.warning("Could not delete %s: %s", path, e)


def render_hot_clips(
    video_chunk_path: Path,
    hot_clips: List[dict],
    transcript: dict,
    auto_upload: bool = False,
) -> List[Path]:
    """Bridge into the existing rendering pipeline (vision.py facecam detection + process.py
    FFmpeg rendering) for exactly the flagged hot clips, cut straight from this chunk's own
    recorded video. If `auto_upload`, each rendered clip is also pushed to TikTok. Either
    way, a Discord notification is sent at the end of the chain for every rendered clip —
    whether the upload succeeded, failed, or wasn't attempted at all."""
    # Must match the *_clips.json path process_module.process() derives internally from
    # video_chunk_path's own stem, since that's the file it will read the clips back from.
    clips_path = TEMP_DIR / f"{video_chunk_path.stem}_clips.json"
    clips_path.parent.mkdir(exist_ok=True)
    with open(clips_path, "w", encoding="utf-8") as f:
        json.dump({"clips": hot_clips}, f, ensure_ascii=False)

    try:
        rendered = process_module.process(
            video_chunk_path,
            layout=process_module.LAYOUT_AUTO,
            video_format=process_module.DEFAULT_FORMAT,
            highlight_color=process_module.DEFAULT_HIGHLIGHT_COLOR,
            transcript=transcript,
        )
    except Exception as e:
        logger.error("Auto-render failed for chunk %s: %s", video_chunk_path, e)
        return []

    logger.info(
        "Auto-rendered %d clip(s) from %s -> %s",
        len(rendered), video_chunk_path.name, [p.name for p in rendered],
    )

    for clip, output_path in zip(hot_clips, rendered):
        upload_status = "rendered"
        hashtags = clip.get("hashtags") or tiktok_uploader.DEFAULT_HASHTAGS

        if auto_upload:
            success = tiktok_uploader.try_upload_clip(
                output_path, clip.get("description", clip["title"]), hashtags
            )
            upload_status = "uploaded" if success else "failed"

        notify.send_notification(
            title=clip["title"],
            energy=clip.get("energy_rating", 0),
            filepath=output_path,
            upload_status=upload_status,
            description=clip.get("description", ""),
            hashtags=hashtags,
        )

    return rendered


def process_chunk(
    chunk_path: Path,
    energy_threshold: int = ENERGY_THRESHOLD,
    profile: Optional[dict] = None,
    auto_render: bool = False,
    auto_upload: bool = False,
) -> List[dict]:
    """Extract audio, transcribe, and analyze one chunk; return the clips that clear the
    energy threshold. Optionally auto-renders them straight from the chunk's own video.

    Narrative coherence (logical start/end, no mid-sentence cuts, payoff integrity) is
    already enforced by analyze.py's system prompt — any clip the LLM returns at all
    already satisfies those rules, so here we only need to additionally filter by energy.
    """
    logger.info("Processing chunk %s", chunk_path)

    with _processing_lock:
        audio_path = None
        try:
            audio_path = ingest.extract_audio(chunk_path)
            transcription_path = transcribe.transcribe(audio_path)
            clips_path = analyze.analyze(transcription_path, audio_path=audio_path, profile=profile)
            with open(clips_path, "r", encoding="utf-8") as f:
                clips_data = json.load(f)
        except Exception as e:
            logger.error("Failed to analyze chunk %s: %s", chunk_path, e)
            _cleanup(audio_path)
            _cleanup(chunk_path)
            return []

        # The wav was only needed transiently for transcription; the .ts chunk stays
        # around a little longer in case a hot clip needs rendering from it.
        _cleanup(audio_path)

        hot_clips = [
            clip for clip in clips_data.get("clips", [])
            if clip.get("energy_rating", 0) >= energy_threshold
        ]

        if not hot_clips:
            logger.info("No clips with energy_rating >= %d found in %s", energy_threshold, chunk_path.name)
            _cleanup(chunk_path)
            return []

        for clip in hot_clips:
            logger.info(
                "🔥 HOT CLIP in %s: '%s' (energy=%d, viral=%d) [%.1fs-%.1fs]",
                chunk_path.name, clip["title"], clip["energy_rating"], clip["viral_score"],
                clip["start_time"], clip["end_time"],
            )
            log_hot_clip(chunk_path, clip)

        if auto_render:
            transcript = analyze.load_transcript(transcription_path)
            render_hot_clips(chunk_path, hot_clips, transcript, auto_upload)
            _cleanup(chunk_path)
        else:
            logger.info("Keeping raw chunk %s for manual rendering (auto-render disabled)", chunk_path)

        return hot_clips


def _drain_completed(futures: List[concurrent.futures.Future]) -> List[concurrent.futures.Future]:
    """Log exceptions from finished background jobs and drop them from the tracking list,
    so a long-running session doesn't accumulate futures forever and errors don't vanish
    silently."""
    still_running = []
    for future in futures:
        if future.done():
            exc = future.exception()
            if exc:
                logger.error("Background chunk processing raised: %s", exc)
        else:
            still_running.append(future)
    return still_running


def watch_stream(
    url: str,
    chunk_duration: int = DEFAULT_CHUNK_DURATION,
    max_chunks: Optional[int] = None,
    energy_threshold: int = ENERGY_THRESHOLD,
    profile: Optional[dict] = None,
    auto_render: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
    auto_upload: bool = False,
) -> None:
    """Record chunks continuously; each finished chunk is handed to a background worker
    for transcription/analysis (and optional rendering/upload) while the NEXT chunk starts
    recording immediately — recording is never blocked waiting on processing."""
    logger.info(
        "Starting stream watcher for %s (chunk_duration=%ds, energy_threshold=%d, "
        "profile=%s, auto_render=%s, auto_upload=%s, max_workers=%d)",
        url, chunk_duration, energy_threshold,
        profile.get("name") if profile else None, auto_render, auto_upload, max_workers,
    )

    chunk_count = 0
    futures: List[concurrent.futures.Future] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="chunk-worker") as executor:
        try:
            while max_chunks is None or chunk_count < max_chunks:
                try:
                    chunk_path = record_stream_chunk(url, chunk_duration)
                except Exception as e:
                    logger.error("Failed to record chunk, retrying in %ds: %s", RETRY_DELAY_SECONDS, e)
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue

                futures.append(
                    executor.submit(
                        process_chunk, chunk_path, energy_threshold, profile, auto_render, auto_upload
                    )
                )
                futures = _drain_completed(futures)
                chunk_count += 1
        finally:
            logger.info("Waiting for in-flight chunk processing to finish...")
            concurrent.futures.wait(futures)
            _drain_completed(futures)


def main():
    parser = argparse.ArgumentParser(
        description="Watch a livestream, chunk it, and flag (optionally auto-render) high-energy viral moments."
    )
    parser.add_argument("--url", default=None, help="Livestream URL (Twitch, YouTube, etc.); falls back to the profile's stream_url")
    parser.add_argument("--profile", default=None, help="Streamer profile name (profiles/<name>.json)")
    parser.add_argument(
        "--chunk-duration", type=int, default=DEFAULT_CHUNK_DURATION, help="Seconds per recorded chunk"
    )
    parser.add_argument(
        "--max-chunks", type=int, default=None, help="Stop after N chunks (omit to run until interrupted)"
    )
    parser.add_argument(
        "--energy-threshold", type=int, default=None,
        help="Minimum energy_rating to flag a clip (defaults to the profile's, or 7 without a profile)",
    )
    parser.add_argument(
        "--auto-render", action="store_true",
        help="Immediately render (facecam crop, subtitles, FFmpeg) any hot clips found",
    )
    parser.add_argument(
        "--auto-upload", action="store_true",
        help="After auto-rendering, also upload each clip to TikTok via browser automation "
        "(requires --auto-render, and exported session cookies in cookies.json)",
    )
    parser.add_argument(
        "--max-workers", type=int, default=DEFAULT_MAX_WORKERS,
        help="Background worker threads for chunk processing",
    )
    args = parser.parse_args()

    if args.auto_upload and not args.auto_render:
        parser.error("--auto-upload requires --auto-render (there's nothing rendered to upload otherwise)")

    profile_dict = None
    if args.profile:
        profile_dict = profiles.load_profile_or_fallback(args.profile).model_dump()

    url = args.url or (profile_dict["stream_url"] if profile_dict else None)
    if not url:
        parser.error("--url is required unless --profile points to a profile with a stream_url set")

    energy_threshold = args.energy_threshold
    if energy_threshold is None:
        energy_threshold = profile_dict["energy_threshold"] if profile_dict else ENERGY_THRESHOLD

    watch_stream(
        url, args.chunk_duration, args.max_chunks, energy_threshold,
        profile_dict, args.auto_render, args.max_workers, args.auto_upload,
    )


if __name__ == "__main__":
    main()
