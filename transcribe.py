"""Transcription step: run faster-whisper (CPU) on the extracted .wav and write a structured JSON transcript."""

import argparse
import json
import logging
import os
from pathlib import Path

from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
OUTPUT_PATH = TEMP_DIR / "transcription.json"
MODEL_SIZE = "base"


def transcribe(audio_path: Path, model_size: str = MODEL_SIZE) -> Path:
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    cpu_threads = os.cpu_count() or 4
    logger.info("Loading faster-whisper model '%s' on CPU (int8, %d threads)", model_size, cpu_threads)
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=cpu_threads)

    logger.info("Transcribing %s", audio_path)
    segments, info = model.transcribe(str(audio_path), word_timestamps=True)

    logger.info("Detected language '%s' (probability %.2f)", info.language, info.language_probability)

    result_segments = []
    for segment in segments:
        words = [
            {"text": word.word.strip(), "start": word.start, "end": word.end}
            for word in (segment.words or [])
        ]
        result_segments.append(
            {
                "text": segment.text.strip(),
                "start": segment.start,
                "end": segment.end,
                "words": words,
            }
        )

    if not result_segments:
        raise RuntimeError(f"Transcription produced no segments for: {audio_path}")

    TEMP_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"language": info.language, "segments": result_segments}, f, ensure_ascii=False, indent=2)

    logger.info("Transcription complete: %s (%d segments)", OUTPUT_PATH, len(result_segments))
    return OUTPUT_PATH


def main():
    parser = argparse.ArgumentParser(description="Transcribe a .wav file with faster-whisper on CPU.")
    parser.add_argument("audio", type=Path, help="Path to the input .wav file (e.g. temp/<video_stem>.wav)")
    parser.add_argument("--model", default=MODEL_SIZE, help="faster-whisper model size (e.g. base, small)")
    args = parser.parse_args()

    transcribe(args.audio, args.model)


if __name__ == "__main__":
    main()
