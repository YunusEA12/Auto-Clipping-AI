"""Processing step: burn subtitles into 9:16 split-screen clips (facecam top / gameplay bottom) via FFmpeg."""

import argparse
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

import cv2

from vision import get_facecam_coordinates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
OUTPUT_DIR = Path("output")
CLIPS_PATH = TEMP_DIR / "clips.json"
TRANSCRIPTION_PATH = TEMP_DIR / "transcription.json"

# 9:16 output canvas
OUTPUT_W = 1080
OUTPUT_H = 1920

ASS_HEADER = """[Script Info]
Title: Auto-generated subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def format_ass_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return slug or "clip"


def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_ass_for_clip(clip: dict, transcript: dict, index: int) -> Path:
    clip_start = clip["start_time"]
    clip_end = clip["end_time"]
    segments = transcript.get("segments", [])

    events = []
    for seg in segments:
        if seg["end"] <= clip_start or seg["start"] >= clip_end:
            continue

        rel_start = max(seg["start"], clip_start) - clip_start
        rel_end = min(seg["end"], clip_end) - clip_start
        if rel_end <= rel_start:
            continue

        text = seg["text"].strip().replace("\n", " ")
        if not text:
            continue

        events.append(
            f"Dialogue: 0,{format_ass_timestamp(rel_start)},{format_ass_timestamp(rel_end)},Default,,0,0,0,,{text}"
        )

    if not events:
        raise RuntimeError(f"No subtitle events found for clip {index} ({clip_start}-{clip_end}s)")

    ass_path = TEMP_DIR / f"clip_{index}.ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        f.write("\n".join(events))
        f.write("\n")

    logger.info("Wrote subtitles: %s (%d lines)", ass_path, len(events))
    return ass_path


def get_video_dimensions(video_path: Path) -> Tuple[int, int]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()

    if width <= 0 or height <= 0:
        raise RuntimeError(f"Could not determine dimensions for video: {video_path}")
    return width, height


def escape_subtitles_path(path: Path) -> str:
    p = str(path.resolve()).replace("\\", "/")
    p = p.replace(":", "\\:")
    return p


def render_clip(
    source_video: Path,
    clip: dict,
    ass_path: Path,
    index: int,
    facecam_box: Tuple[int, int, int, int],
    gameplay_box: Tuple[int, int, int, int],
) -> Path:
    start = clip["start_time"]
    end = clip["end_time"]
    title_slug = slugify(clip["title"])
    output_path = OUTPUT_DIR / f"clip_{index}_{title_slug}.mp4"

    face_x, face_y, face_w, face_h = facecam_box
    game_x, game_y, game_w, game_h = gameplay_box

    filter_complex = (
        f"[0:v]crop={face_w}:{face_h}:{face_x}:{face_y},"
        f"scale={OUTPUT_W}:{OUTPUT_H // 2}[face];"
        f"[0:v]crop={game_w}:{game_h}:{game_x}:{game_y},"
        f"scale={OUTPUT_W}:{OUTPUT_H // 2}[game];"
        f"[face][game]vstack=inputs=2[stacked];"
        f"[stacked]subtitles='{escape_subtitles_path(ass_path)}'[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", str(source_video),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(output_path),
    ]

    logger.info("Rendering clip %d -> %s", index, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        logger.error("FFmpeg failed for clip %d: %s", index, result.stderr[-4000:])
        raise RuntimeError(f"FFmpeg failed for clip {index} (exit code {result.returncode})")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Render produced empty output for clip {index}: {output_path}")

    logger.info("Finished clip %d: %s", index, output_path)
    return output_path


def find_source_video(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"Source video not found: {explicit}")
        return explicit

    candidates: List[Path] = []
    for ext in ("*.mp4", "*.mkv"):
        candidates.extend(Path(".").glob(ext))

    if not candidates:
        raise FileNotFoundError(
            "No source video found in the project root. Pass one explicitly with --video."
        )
    if len(candidates) > 1:
        raise RuntimeError(
            f"Multiple candidate source videos found ({[str(c) for c in candidates]}); pass one explicitly with --video."
        )
    return candidates[0]


def process(source_video: Path | None = None) -> List[Path]:
    clips_data = load_json(CLIPS_PATH)
    transcript = load_json(TRANSCRIPTION_PATH)
    video_path = find_source_video(source_video)

    clips = clips_data.get("clips", [])
    if not clips:
        raise RuntimeError(f"No clips found in {CLIPS_PATH}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    video_w, video_h = get_video_dimensions(video_path)
    # Gameplay is treated as the bottom half of the source frame; facecam is detected per clip.
    gameplay_box = (0, video_h // 2, video_w, video_h - video_h // 2)

    outputs = []
    for i, clip in enumerate(clips, start=1):
        facecam_box = get_facecam_coordinates(str(video_path), clip["start_time"])
        ass_path = build_ass_for_clip(clip, transcript, i)
        output_path = render_clip(video_path, clip, ass_path, i, facecam_box, gameplay_box)
        outputs.append(output_path)

    logger.info("Processing complete: %d clips rendered to %s", len(outputs), OUTPUT_DIR)
    return outputs


def main():
    parser = argparse.ArgumentParser(description="Render 9:16 split-screen clips with burned-in subtitles.")
    parser.add_argument("--video", type=Path, default=None, help="Path to the source video (auto-detected if omitted)")
    args = parser.parse_args()

    process(args.video)


if __name__ == "__main__":
    main()
