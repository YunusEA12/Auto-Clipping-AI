"""Processing step: burn word-by-word highlighted subtitles into split-screen/blur-background clips via FFmpeg."""

import argparse
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import cv2

import vision

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
OUTPUT_DIR = Path("output")
CLIPS_PATH = TEMP_DIR / "clips.json"
TRANSCRIPTION_PATH = TEMP_DIR / "transcription.json"

# Supported output canvases, keyed by aspect ratio label.
VIDEO_FORMATS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}
DEFAULT_FORMAT = "9:16"

LAYOUT_SPLIT_SCREEN = "split_screen"
LAYOUT_BLUR_BACKGROUND = "blur_background"
LAYOUT_AUTO = "auto"
SELECTABLE_LAYOUTS = (LAYOUT_SPLIT_SCREEN, LAYOUT_BLUR_BACKGROUND, LAYOUT_AUTO)

HIGHLIGHT_COLORS = {
    "Gelb (Hormozi)": "00FFFF",
    "Neon Grün": "00FF66",
    "Weiß": "FFFFFF",
}
DEFAULT_HIGHLIGHT_COLOR = HIGHLIGHT_COLORS["Gelb (Hormozi)"]
WORD_BASE_COLOR = "FFFFFF"
WORDS_PER_BLOCK = 4

ASS_HEADER_TEMPLATE = """[Script Info]
Title: Auto-generated subtitles
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,80,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,5,50,50,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Where the subtitle block sits vertically, as a fraction of the canvas height —
# the TikTok "lower third", clear of both the top facecam split and bottom platform UI icons.
SUBTITLE_Y_RATIO = 0.70


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


def _fallback_segment_event(seg: dict, clip_start: float, clip_end: float, position_tag: str) -> Optional[str]:
    """Whole-segment subtitle line used when word-level timestamps are unavailable."""
    rel_start = max(seg["start"], clip_start) - clip_start
    rel_end = min(seg["end"], clip_end) - clip_start
    if rel_end <= rel_start:
        return None

    text = seg["text"].strip().replace("\n", " ").upper()
    if not text:
        return None

    return f"Dialogue: 0,{format_ass_timestamp(rel_start)},{format_ass_timestamp(rel_end)},Default,,0,0,0,,{position_tag}{text}"


def _word_block_events(words: list, clip_start: float, clip_end: float, position_tag: str, highlight_color: str) -> List[str]:
    """Karaoke-style word-by-word highlighting: one Dialogue line per word, timed to that word."""
    events = []
    for block_start in range(0, len(words), WORDS_PER_BLOCK):
        block = words[block_start: block_start + WORDS_PER_BLOCK]
        block_texts = [w["text"].strip().upper() for w in block]

        for i, word in enumerate(block):
            w_start = word["start"]
            w_end = word["end"]
            if w_end <= clip_start or w_start >= clip_end:
                continue

            rel_start = max(w_start, clip_start) - clip_start
            rel_end = min(w_end, clip_end) - clip_start
            if rel_end <= rel_start:
                continue

            rendered = [
                f"{{\\c&H{highlight_color}&}}{text}{{\\c&H{WORD_BASE_COLOR}&}}" if j == i else text
                for j, text in enumerate(block_texts)
            ]
            line_text = " ".join(rendered)

            events.append(
                f"Dialogue: 0,{format_ass_timestamp(rel_start)},{format_ass_timestamp(rel_end)},Default,,0,0,0,,{position_tag}{line_text}"
            )

    return events


def build_ass_for_clip(
    clip: dict,
    transcript: dict,
    index: int,
    highlight_color: str = DEFAULT_HIGHLIGHT_COLOR,
    output_w: int = 1080,
    output_h: int = 1920,
) -> Path:
    clip_start = clip["start_time"]
    clip_end = clip["end_time"]
    segments = transcript.get("segments", [])

    # Lower third, where TikTok viewers expect captions (e.g. y=1350-1400 at 1080x1920).
    pos_x = output_w // 2
    pos_y = int(output_h * SUBTITLE_Y_RATIO)
    position_tag = f"{{\\an5\\pos({pos_x},{pos_y})}}"

    events = []
    for seg in segments:
        if seg["end"] <= clip_start or seg["start"] >= clip_end:
            continue

        words = seg.get("words") or []
        if not words:
            event = _fallback_segment_event(seg, clip_start, clip_end, position_tag)
            if event:
                events.append(event)
            continue

        events.extend(_word_block_events(words, clip_start, clip_end, position_tag, highlight_color))

    if not events:
        logger.warning(
            "No transcribed words found for clip %d (%.2fs-%.2fs); writing subtitle-free .ass",
            index, clip_start, clip_end,
        )

    ass_path = TEMP_DIR / f"clip_{index}.ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER_TEMPLATE.format(width=output_w, height=output_h))
        if events:
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


def build_filter_complex(
    layout: str,
    ass_path: Path,
    output_w: int,
    output_h: int,
    facecam_box: Tuple[int, int, int, int] | None = None,
    gameplay_box: Tuple[int, int, int, int] | None = None,
) -> str:
    subtitles = escape_subtitles_path(ass_path)

    if layout == LAYOUT_SPLIT_SCREEN:
        # Both halves go through the identical pipeline: crop out the source region,
        # then a "cover" fit — scale up with force_original_aspect_ratio=increase (so no
        # axis is ever stretched) and crop the overhang — to land on EXACTLY output_w x
        # half_h (1080x960 for the default 9:16 canvas). setsar=1 pins square pixels so
        # a player never re-stretches either half after they're vstack'd together.
        # Single stacking step (vstack), no separate canvas/overlay scaffolding.
        face_x, face_y, face_w, face_h = facecam_box
        game_x, game_y, game_w, game_h = gameplay_box
        half_h = output_h // 2

        def _cover_fit(label: str, x: int, y: int, w: int, h: int) -> str:
            return (
                f"[0:v]crop={w}:{h}:{x}:{y},"
                f"scale={output_w}:{half_h}:force_original_aspect_ratio=increase,"
                f"crop={output_w}:{half_h},setsar=1[{label}]"
            )

        return (
            f"{_cover_fit('face', face_x, face_y, face_w, face_h)};"
            f"{_cover_fit('game', game_x, game_y, game_w, game_h)};"
            f"[face][game]vstack=inputs=2[stacked];"
            f"[stacked]subtitles='{subtitles}'[outv]"
        )

    if layout == LAYOUT_BLUR_BACKGROUND:
        # setsar=1 right at the source reference (not just on the outputs) so a source
        # with non-square sample-aspect-ratio metadata can never throw off the "-2"
        # auto-height math below — the likely cause of an unexpectedly tiny foreground.
        # Blur a heavily downscaled copy, then upscale — far cheaper than blurring full-res.
        small_w, small_h = output_w // 4, output_h // 4
        return (
            f"[0:v]setsar=1,scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
            f"crop={small_w}:{small_h},boxblur=20:20,scale={output_w}:{output_h},setsar=1[bg];"
            # Full width, height preserved proportionally and centered — the sharp
            # foreground stays the dominant element instead of a small lost strip.
            f"[0:v]setsar=1,scale={output_w}:-2,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[stacked];"
            f"[stacked]subtitles='{subtitles}'[outv]"
        )

    raise ValueError(f"Unknown layout: {layout}")


def render_clip(
    source_video: Path,
    clip: dict,
    ass_path: Path,
    index: int,
    layout: str,
    output_w: int,
    output_h: int,
    facecam_box: Tuple[int, int, int, int] | None = None,
    gameplay_box: Tuple[int, int, int, int] | None = None,
) -> Path:
    start = clip["start_time"]
    end = clip["end_time"]
    title_slug = slugify(clip["title"])
    output_path = OUTPUT_DIR / f"clip_{index}_{title_slug}.mp4"

    filter_complex = build_filter_complex(layout, ass_path, output_w, output_h, facecam_box, gameplay_box)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", str(source_video),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        str(output_path),
    ]

    logger.info("Rendering clip %d (layout=%s) -> %s", index, layout, output_path)
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


def resolve_layout(layout: str, video_path: Path, clip_start: float) -> str:
    if layout != LAYOUT_AUTO:
        return layout

    face_present = vision.has_face(str(video_path), clip_start)
    resolved = LAYOUT_SPLIT_SCREEN if face_present else LAYOUT_BLUR_BACKGROUND
    logger.info("Auto-layout at %.2fs: face_present=%s -> %s", clip_start, face_present, resolved)
    return resolved


def process(
    source_video: Path | None = None,
    layout: str = LAYOUT_SPLIT_SCREEN,
    video_format: str = DEFAULT_FORMAT,
    highlight_color: str = DEFAULT_HIGHLIGHT_COLOR,
) -> List[Path]:
    if layout not in SELECTABLE_LAYOUTS:
        raise ValueError(f"Unknown layout '{layout}', expected one of {SELECTABLE_LAYOUTS}")
    if video_format not in VIDEO_FORMATS:
        raise ValueError(f"Unknown video format '{video_format}', expected one of {tuple(VIDEO_FORMATS)}")

    output_w, output_h = VIDEO_FORMATS[video_format]

    clips_data = load_json(CLIPS_PATH)
    transcript = load_json(TRANSCRIPTION_PATH)
    video_path = find_source_video(source_video)

    clips = clips_data.get("clips", [])
    if not clips:
        raise RuntimeError(f"No clips found in {CLIPS_PATH}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    gameplay_box = None
    if layout in (LAYOUT_SPLIT_SCREEN, LAYOUT_AUTO):
        video_w, video_h = get_video_dimensions(video_path)
        # Gameplay uses the FULL source frame (not a fixed bottom-half slice): a fixed
        # bottom-half box would frequently overlap the actual facecam region — streamer
        # webcams are commonly positioned in a bottom corner — causing both halves to
        # show near-identical content. The full frame is always visually distinct from
        # the zoomed-in facecam crop above it, so the two halves never duplicate.
        gameplay_box = (0, 0, video_w, video_h)

    outputs = []
    for i, clip in enumerate(clips, start=1):
        effective_layout = resolve_layout(layout, video_path, clip["start_time"])

        facecam_box = None
        if effective_layout == LAYOUT_SPLIT_SCREEN:
            facecam_box = vision.get_facecam_coordinates(str(video_path), clip["start_time"])

        ass_path = build_ass_for_clip(clip, transcript, i, highlight_color, output_w, output_h)
        output_path = render_clip(
            video_path, clip, ass_path, i, effective_layout, output_w, output_h, facecam_box, gameplay_box
        )
        outputs.append(output_path)

    logger.info("Processing complete: %d clips rendered to %s", len(outputs), OUTPUT_DIR)
    return outputs


def main():
    parser = argparse.ArgumentParser(description="Render clips with burned-in, word-by-word highlighted subtitles.")
    parser.add_argument("--video", type=Path, default=None, help="Path to the source video (auto-detected if omitted)")
    parser.add_argument("--layout", choices=SELECTABLE_LAYOUTS, default=LAYOUT_SPLIT_SCREEN, help="Video layout mode")
    parser.add_argument("--format", dest="video_format", choices=tuple(VIDEO_FORMATS), default=DEFAULT_FORMAT, help="Output aspect ratio")
    parser.add_argument("--highlight-color", default=DEFAULT_HIGHLIGHT_COLOR, help="ASS BGR hex color for the active word (e.g. 00FFFF)")
    args = parser.parse_args()

    process(args.video, args.layout, args.video_format, args.highlight_color)


if __name__ == "__main__":
    main()
