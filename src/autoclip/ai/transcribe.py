"""Transcription step: run faster-whisper (CPU) on the extracted .wav and write a structured JSON transcript."""

import argparse
import json
import logging
import math
import os
from pathlib import Path

from faster_whisper import WhisperModel
from filelock import FileLock

from autoclip import runtime
from autoclip.core import atomic_io, logging_setup
from autoclip.core.inference_concurrency import inference_slot

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

TEMP_DIR = runtime.path("temp")
OUTPUT_PATH = TEMP_DIR / "transcription.json"
MODEL_SIZE = "base"


def whisper_cpu_threads() -> int:
    """Bound each streamer's CPU use; six workers must not each claim every core."""
    available = os.cpu_count() or 2
    raw = os.environ.get("WHISPER_CPU_THREADS", "2")
    try:
        requested = int(raw)
        if requested < 1:
            raise ValueError
    except ValueError:
        logger.warning("Invalid WHISPER_CPU_THREADS; using 2")
        requested = 2
    return min(available, requested)


def transcription_path_for(audio_path: Path) -> Path:
    """Per-video transcript path (e.g. temp/my_video_transcription.json) so re-processing
    the same video can be recognized and skipped instead of re-running Whisper."""
    return TEMP_DIR / f"{audio_path.stem}_transcription.json"


def _consume_segments(segments) -> list:
    """Drains faster-whisper's lazy segment generator into plain dicts. Split out from
    transcribe() so it can be called a second time on retry (see transcribe()'s IndexError
    handler) without duplicating this shape-conversion logic."""
    return [
        {
            "text": segment.text.strip(),
            "start": segment.start,
            "end": segment.end,
            "words": [
                {"text": word.word.strip(), "start": word.start, "end": word.end} for word in (segment.words or [])
            ],
        }
        for segment in segments
    ]


def transcribe(audio_path: Path, model_size: str = MODEL_SIZE) -> Path:
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    output_path = transcription_path_for(audio_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output_path) + ".lock", timeout=1800):
        if output_path.exists():
            try:
                validate_transcript(json.loads(output_path.read_text(encoding="utf-8")))
                logger.info("Reusing validated transcript: %s", output_path)
                return output_path
            except (ValueError, OSError, TypeError, KeyError):
                logger.warning("Invalid transcript cache; rebuilding %s", output_path)
        with inference_slot():
            return _transcribe_uncached(audio_path, output_path, model_size)


def validate_transcript(document: dict) -> None:
    """Reject empty, non-finite or reversed timestamps before downstream selection."""
    if not isinstance(document, dict) or not isinstance(document.get("segments"), list) or not document["segments"]:
        raise ValueError("Transcript contains no segments")
    previous = -1.0
    for segment in document["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError("Invalid transcript text")
        start, end = segment.get("start"), segment.get("end")
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)):
            raise ValueError("Invalid transcript timestamp")
        if start < 0 or end <= start or start < previous:
            raise ValueError("Invalid transcript ordering")
        previous = start
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError("Invalid word list")
        # Whisper's forced alignment can place words across segment boundaries.
        # Preserve those measured times; validate each word sequence independently.
        word_previous = 0.0
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                raise ValueError("Invalid word")
            a, b = word.get("start"), word.get("end")
            if any(type(t) not in (int, float) or not math.isfinite(t) for t in (a, b)):
                raise ValueError("Invalid word timestamp")
            if a < 0 or b < a or a < word_previous:
                raise ValueError("Invalid word ordering")
            word_previous = a


def _transcribe_uncached(audio_path: Path, output_path: Path, model_size: str) -> Path:
    cpu_threads = whisper_cpu_threads()
    logger.info("Loading faster-whisper model '%s' on CPU (int8, %d threads)", model_size, cpu_threads)
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=cpu_threads)

    logger.info("Transcribing %s", audio_path)
    segments, info = model.transcribe(str(audio_path), word_timestamps=True)

    logger.info("Detected language '%s' (probability %.2f)", info.language, info.language_probability)

    # Plain list of dicts (start/end/text/words) rather than faster-whisper's own objects —
    # this is the editable contract: the Streamlit subtitle editor and process.py's renderer
    # both read/write this exact JSON shape instead of anything tied to the transcription lib.
    try:
        result_segments = _consume_segments(segments)
    except IndexError as e:
        if "boolean index" not in str(e):
            raise
        # Confirmed live, 2026-08-21 (crashed a whole cycle, zero clips produced from that
        # 3-minute chunk) — a real bug in faster-whisper 1.2.1's own find_alignment()
        # (site-packages/faster_whisper/transcribe.py): when the forced-alignment model
        # returns zero (text, time) index pairs for a segment with >1 word token — plausible
        # for noisy/non-speech-heavy stretches, common in a live gaming stream (crowd noise,
        # game audio, laughter) — `time_indices` ends up shape (0,) while the derived boolean
        # `jumps` mask ends up shape (1,), raising exactly this IndexError. `segments` is a
        # lazy generator, so this can surface mid-iteration on ANY segment, not just the
        # first — nothing in this codebase can fix the library's own alignment math, but
        # word_timestamps=False skips that code path entirely. Every downstream consumer
        # already tolerates a segment with no word-level timestamps (the `segment.words or []`
        # inside _consume_segments, and process.py's own subtitle renderer), so this is a real
        # quality tradeoff (no per-word caption timing for this chunk) rather than losing the
        # whole chunk's clips over a library edge case.
        logger.warning(
            "faster-whisper's word-alignment step hit a known internal bug (%s) — retrying %s "
            "without word-level timestamps.",
            e,
            audio_path,
        )
        segments, info = model.transcribe(str(audio_path), word_timestamps=False)
        result_segments = _consume_segments(segments)

    if not result_segments:
        raise RuntimeError(f"Transcription produced no segments for: {audio_path}")

    document = {"language": info.language, "segments": result_segments}
    validate_transcript(document)
    atomic_io.atomic_write_json(output_path, document)

    logger.info("Transcription complete: %s (%d segments)", output_path, len(result_segments))
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Transcribe a .wav file with faster-whisper on CPU.")
    parser.add_argument("audio", type=Path, help="Path to the input .wav file (e.g. temp/<video_stem>.wav)")
    parser.add_argument("--model", default=MODEL_SIZE, help="faster-whisper model size (e.g. base, small)")
    args = parser.parse_args()

    transcribe(args.audio, args.model)


if __name__ == "__main__":
    main()
