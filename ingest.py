"""Ingestion step: extract the audio track from a local video into temp/ as a .wav file."""

import argparse
import logging
from pathlib import Path

import ffmpeg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")


def extract_audio(video_path: Path) -> Path:
    if not video_path.exists():
        raise FileNotFoundError(f"Input video not found: {video_path}")

    TEMP_DIR.mkdir(exist_ok=True)
    output_path = TEMP_DIR / f"{video_path.stem}.wav"

    logger.info("Extracting audio from %s -> %s", video_path, output_path)
    try:
        (
            ffmpeg
            .input(str(video_path))
            .output(str(output_path), acodec="pcm_s16le", ac=1, ar="16000")
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        logger.error("FFmpeg failed: %s", e.stderr.decode(errors="ignore"))
        raise

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Audio extraction failed, empty output: {output_path}")

    logger.info("Audio extraction complete: %s", output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Extract audio track from a local video file.")
    parser.add_argument("video", type=Path, help="Path to the input video file")
    args = parser.parse_args()

    extract_audio(args.video)


if __name__ == "__main__":
    main()
