"""Live-stream watcher prototype: chunk a livestream via streamlink, transcribe + analyze
each chunk, and flag high-energy moments for later rendering.

Runs standalone from the terminal, independent of the Streamlit UI:
    python stream_watcher.py --url https://twitch.tv/<channel>
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import analyze
import transcribe

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
HOT_CLIPS_LOG_PATH = TEMP_DIR / "live_hot_clips.jsonl"

DEFAULT_CHUNK_DURATION = 180  # seconds (3 minutes)
RETRY_DELAY_SECONDS = 15
ENERGY_THRESHOLD = 7  # clip.energy_rating >= this counts as a "hot clip" worth flagging


def _resolve_streamlink_path() -> str:
    """Find the streamlink executable even when the venv isn't "activated" (no venv/Scripts
    on PATH) — the common case when this project is run as `venv\\Scripts\\python.exe ...`.
    pip installs console scripts next to the interpreter, so check there too."""
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
    """Pull `duration` seconds of audio from a live stream via streamlink -> ffmpeg,
    saved as temp/live_chunk_[TIMESTAMP].wav (matches transcribe.py's expected input)."""
    TEMP_DIR.mkdir(exist_ok=True)
    timestamp = int(time.time())
    output_path = TEMP_DIR / f"live_chunk_{timestamp}.wav"

    streamlink_cmd = [_resolve_streamlink_path(), url, "best", "--stdout", "--quiet"]
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", "pipe:0",
        "-t", str(duration),
        "-vn",
        "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
        str(output_path),
    ]

    logger.info("Recording %ds chunk from %s -> %s", duration, url, output_path)
    streamlink_proc = subprocess.Popen(
        streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    try:
        ffmpeg_result = subprocess.run(
            ffmpeg_cmd,
            stdin=streamlink_proc.stdout,
            capture_output=True,
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
        stderr_tail = ffmpeg_result.stderr[-2000:].decode(errors="ignore") if ffmpeg_result.stderr else ""
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


def process_chunk(chunk_path: Path, energy_threshold: int = ENERGY_THRESHOLD) -> List[dict]:
    """Transcribe + analyze one chunk, return the clips that clear the energy threshold.

    Narrative coherence (logical start/end, no mid-sentence cuts, payoff integrity) is
    already enforced by analyze.py's system prompt — any clip the LLM returns at all
    already satisfies those rules, so here we only need to additionally filter by energy.
    """
    logger.info("Processing chunk %s", chunk_path)
    try:
        transcription_path = transcribe.transcribe(chunk_path)
        clips_path = analyze.analyze(transcription_path, audio_path=chunk_path)
    except Exception as e:
        logger.error("Failed to analyze chunk %s: %s", chunk_path, e)
        return []

    with open(clips_path, "r", encoding="utf-8") as f:
        clips_data = json.load(f)

    hot_clips = [
        clip for clip in clips_data.get("clips", [])
        if clip.get("energy_rating", 0) >= energy_threshold
    ]

    if hot_clips:
        for clip in hot_clips:
            logger.info(
                "🔥 HOT CLIP in %s: '%s' (energy=%d, viral=%d) [%.1fs-%.1fs]",
                chunk_path.name, clip["title"], clip["energy_rating"], clip["viral_score"],
                clip["start_time"], clip["end_time"],
            )
            log_hot_clip(chunk_path, clip)
    else:
        logger.info("No clips with energy_rating >= %d found in %s", energy_threshold, chunk_path.name)

    return hot_clips


def watch_stream(
    url: str,
    chunk_duration: int = DEFAULT_CHUNK_DURATION,
    max_chunks: Optional[int] = None,
    energy_threshold: int = ENERGY_THRESHOLD,
) -> None:
    """Continuously record and analyze chunks until `max_chunks` is hit (or forever)."""
    logger.info(
        "Starting stream watcher for %s (chunk_duration=%ds, energy_threshold=%d)",
        url, chunk_duration, energy_threshold,
    )
    chunk_count = 0
    while max_chunks is None or chunk_count < max_chunks:
        try:
            chunk_path = record_stream_chunk(url, chunk_duration)
        except Exception as e:
            logger.error("Failed to record chunk, retrying in %ds: %s", RETRY_DELAY_SECONDS, e)
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        # Sequential by design for this prototype: analyze the chunk we just captured
        # before recording the next one. A future version could record chunk N+1 in a
        # background thread/process while chunk N is still being transcribed/analyzed.
        process_chunk(chunk_path, energy_threshold)
        chunk_count += 1


def main():
    parser = argparse.ArgumentParser(
        description="Watch a livestream, chunk it, and flag high-energy viral moments."
    )
    parser.add_argument("--url", required=True, help="Livestream URL (Twitch, YouTube, etc.)")
    parser.add_argument(
        "--chunk-duration", type=int, default=DEFAULT_CHUNK_DURATION, help="Seconds per recorded chunk"
    )
    parser.add_argument(
        "--max-chunks", type=int, default=None, help="Stop after N chunks (omit to run until interrupted)"
    )
    parser.add_argument(
        "--energy-threshold", type=int, default=ENERGY_THRESHOLD, help="Minimum energy_rating to flag a clip"
    )
    args = parser.parse_args()

    watch_stream(args.url, args.chunk_duration, args.max_chunks, args.energy_threshold)


if __name__ == "__main__":
    main()
