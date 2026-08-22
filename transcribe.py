"""Transcription step: run faster-whisper (CPU) on the extracted .wav and write a structured JSON transcript."""

import argparse
import json
import logging
import os
from pathlib import Path

from faster_whisper import WhisperModel

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
OUTPUT_PATH = TEMP_DIR / "transcription.json"
MODEL_SIZE = "base"


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
                {"text": word.word.strip(), "start": word.start, "end": word.end}
                for word in (segment.words or [])
            ],
        }
        for segment in segments
    ]


def transcribe(audio_path: Path, model_size: str = MODEL_SIZE) -> Path:
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    output_path = transcription_path_for(audio_path)
    if output_path.exists():
        logger.info("Transkription bereits vorhanden, überspringe Whisper: %s", output_path)
        return output_path

    cpu_threads = os.cpu_count() or 4
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
            "without word-level timestamps.", e, audio_path,
        )
        segments, info = model.transcribe(str(audio_path), word_timestamps=False)
        result_segments = _consume_segments(segments)

    if not result_segments:
        raise RuntimeError(f"Transcription produced no segments for: {audio_path}")

    TEMP_DIR.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"language": info.language, "segments": result_segments}, f, ensure_ascii=False, indent=2)

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
