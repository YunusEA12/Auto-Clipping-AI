"""Processing step: burn word-by-word highlighted subtitles into split-screen/blur-background clips via FFmpeg."""

import argparse
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import cv2

import atomic_io
import vision

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
OUTPUT_DIR = Path("output")

# Supported output canvases, keyed by aspect ratio label.
VIDEO_FORMATS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}
DEFAULT_FORMAT = "9:16"

LAYOUT_SPLIT_SCREEN = "split_screen"
LAYOUT_BLUR_BACKGROUND = "blur_background"
LAYOUT_FULL_CAM = "full_cam"
LAYOUT_AUTO = "auto"
SELECTABLE_LAYOUTS = (LAYOUT_SPLIT_SCREEN, LAYOUT_BLUR_BACKGROUND, LAYOUT_FULL_CAM, LAYOUT_AUTO)

# Facecam gets the top third of the canvas, gameplay the bottom two-thirds — TikTok's
# standard "reaction on top, content below" reaction-cam layout.
SPLIT_SCREEN_FACE_RATIO = 1 / 3

# Auto-layout decision (2026-08-19): with exactly one face detected, which of full_cam vs
# split_screen applies depends on how much of the SOURCE frame the face already covers, not
# just its presence. A face filling a large share of the frame (a Just-Chatting-style stream
# where the whole picture IS the person, no separate gameplay feed) reads as full_cam
# content; a small corner webcam box over a much bigger gameplay area reads as split_screen.
# Empirical starting point, not derived from a spec — render real clips from both kinds of
# source content and check which side of this threshold they land on before trusting it.
FULL_CAM_MIN_FACE_AREA_RATIO = 0.28

# get_facecam_coordinates()'s default PADDING_FACTOR (2.5) is tuned for split_screen's top-
# third zone — cover-fitting that same padded box into full_cam's much taller full-canvas
# target crops far tighter than intended (more of the padding gets consumed matching the 9:16
# aspect than filling it). A larger factor keeps more headroom/shoulders/background visible
# instead of an overly tight face-only crop. Same "empirical, verify by rendering and
# eyeballing" caveat as vision.PADDING_FACTOR itself.
FULL_CAM_PADDING_FACTOR = 4.5

HIGHLIGHT_COLORS = {
    "Gelb (Hormozi)": "00FFFF",
    "Neon Grün": "00FF66",
    "Weiß": "FFFFFF",
}
DEFAULT_HIGHLIGHT_COLOR = HIGHLIGHT_COLORS["Gelb (Hormozi)"]
WORD_BASE_COLOR = "FFFFFF"
WORDS_PER_BLOCK = 4

# TitleBox style (2026-08-19): BorderStyle=3 ("opaque box") is documented as using BackColour
# to fill the box behind the text — verified live by actually rendering a real clip and
# looking at it (not just trusting the ASS spec), which showed the box filling with
# OutlineColour instead, rendering solid black text-on-black when only BackColour was set to
# white. Both OutlineColour and BackColour are now set to the same white, so the box renders
# correctly regardless of which field this libass build actually consults.
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
Style: TitleBox,Arial Black,58,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,24,0,5,50,50,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Where the white title-box hook sits vertically, as a fraction of canvas height — the upper
# half, but clear of TikTok's own top UI chrome (back arrow, search icon), which occupies
# roughly the top 8-10% at typical zoom. Empirical/eyeballed against the reference posts this
# feature was built from (2026-08-19), same "verify by rendering and looking" caveat as every
# other *_Y_RATIO constant in this file.
TITLE_BOX_Y_RATIO = 0.16

# Where the subtitle block sits vertically, as a fraction of the canvas height — the TikTok
# "lower third", clear of both the top facecam split and bottom platform UI icons. Empirical
# (eyeballed against TikTok's own like/comment/share icon column and caption placement in
# the app, not derived from a published spec), and only checked at the SPLIT_SCREEN_FACE_RATIO
# = 1/3 default — if that ratio is ever retuned significantly, re-check this against the app
# again rather than assuming it still clears the facecam split. Raised from 0.70 to 0.78
# (2026-08-19) to clear both the new TITLE_BOX_Y_RATIO hook above and full_cam's full-frame
# subject (a person's torso can extend further down a full-bleed face crop than gameplay or a
# blurred background ever did, which the original 0.70 was tuned against); push it lower
# (toward 1.0) if captions overlap the platform's icon column, higher if they sit
# uncomfortably close to the bottom edge or start overlapping the title box / a full_cam
# subject's face.
SUBTITLE_Y_RATIO = 0.78


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


# Single source of truth for the rendered-clip filename format — render_clip() below writes
# it, and train_loop.py's extract_frames_for_clips() re-derives it via clip_glob_pattern()
# when no explicit rendered-path dict was handed to it (its standalone-CLI code path). The
# two used to be independently hardcoded in both files, kept in sync only by convention.
def clip_output_filename(index: int, title: str) -> str:
    return f"clip_{index}_{slugify(title)}.mp4"


def clip_glob_pattern(index: int) -> str:
    return f"clip_{index}_*.mp4"


def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _escape_ass_text(text: str) -> str:
    """A literal '{' or '}' in transcribed speech would be parsed by libass as an
    override-tag delimiter, corrupting or swallowing that (or a subsequent) subtitle line
    when burned into the final render (found in review, 2026-08-18). ASS has no native
    escape sequence for a literal brace, and spoken text has no legitimate use for
    override-tag-only characters, so they're stripped rather than escaped."""
    return text.replace("{", "").replace("}", "")


def _fallback_segment_event(seg: dict, clip_start: float, clip_end: float, position_tag: str) -> Optional[str]:
    """Whole-segment subtitle line used when word-level timestamps are unavailable."""
    rel_start = max(seg["start"], clip_start) - clip_start
    rel_end = min(seg["end"], clip_end) - clip_start
    if rel_end <= rel_start:
        return None

    text = _escape_ass_text(seg["text"].strip().replace("\n", " ").upper())
    if not text:
        return None

    return f"Dialogue: 0,{format_ass_timestamp(rel_start)},{format_ass_timestamp(rel_end)},Default,,0,0,0,,{position_tag}{text}"


def _word_block_events(words: list, clip_start: float, clip_end: float, position_tag: str, highlight_color: str) -> List[str]:
    """Karaoke-style word-by-word highlighting: one Dialogue line per word, timed to that word."""
    events = []
    for block_start in range(0, len(words), WORDS_PER_BLOCK):
        block = words[block_start: block_start + WORDS_PER_BLOCK]
        block_texts = [_escape_ass_text(w["text"].strip().upper()) for w in block]

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

    # White title-box hook, upper half of the canvas, visible for the clip's whole duration —
    # separate from the word-by-word subtitle events above (different style, different fixed
    # position, TITLE_BOX_Y_RATIO instead of SUBTITLE_Y_RATIO so the two can never overlap).
    # Reuses clip["title"] (already LLM-generated per clip, e.g. "Krasser Moment!") rather
    # than asking the LLM for a second, separate hook string.
    title_text = _escape_ass_text((clip.get("title") or "").strip())
    if title_text:
        title_pos_y = int(output_h * TITLE_BOX_Y_RATIO)
        title_tag = f"{{\\an5\\pos({pos_x},{title_pos_y})}}"
        clip_duration = clip_end - clip_start
        events.append(
            f"Dialogue: 0,{format_ass_timestamp(0)},{format_ass_timestamp(clip_duration)},TitleBox,,0,0,0,,{title_tag}{title_text}"
        )

    ass_path = TEMP_DIR / f"clip_{index}.ass"
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER_TEMPLATE.format(width=output_w, height=output_h))
        if events:
            f.write("\n".join(events))
            f.write("\n")

    logger.info("Wrote subtitles: %s (%d lines)", ass_path, len(events))
    return ass_path


def _read_video_properties(video_path: Path) -> Tuple[int, int, float, float]:
    """Raw (width, height, fps, frame_count) from a single cv2.VideoCapture open — no
    validation here, since the three public functions below each need a different subset
    and must only raise over the property they actually care about (e.g.
    get_video_dimensions() must not fail a video whose width/height are fine but whose fps
    metadata happens to be unreliable, which does happen with some live-recorded streams)."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    finally:
        cap.release()
    return width, height, fps, frame_count


def get_video_properties(video_path: Path) -> Tuple[int, int, float]:
    """(width, height, duration_seconds), validating all three — the function to reach for
    whenever a caller needs more than one of these for the same file, instead of the
    individual functions below each opening their own separate capture."""
    width, height, fps, frame_count = _read_video_properties(video_path)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Could not determine dimensions for video: {video_path}")
    if fps <= 0 or frame_count <= 0:
        raise RuntimeError(f"Could not determine duration for video: {video_path}")
    return width, height, frame_count / fps


def get_video_dimensions(video_path: Path) -> Tuple[int, int]:
    width, height, _, _ = _read_video_properties(video_path)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Could not determine dimensions for video: {video_path}")
    return width, height


def get_video_duration(video_path: Path) -> float:
    _, _, fps, frame_count = _read_video_properties(video_path)
    if fps <= 0 or frame_count <= 0:
        raise RuntimeError(f"Could not determine duration for video: {video_path}")
    return frame_count / fps


# Near-start, middle, near-end rather than literal 0%/50%/100% — the very first/last frame
# of a render is occasionally black (encoder ramp-up/fade), which would otherwise starve
# the vision critic (train_loop.py) of a usable image for that slot.
PREVIEW_FRAME_FRACTIONS = (0.05, 0.5, 0.95)


def extract_preview_frames(video_path: Path, frames_dir: Path = TEMP_DIR) -> List[Path]:
    """Extract 3 evenly spaced frames (near-start, middle, near-end) from a rendered clip
    via FFmpeg, as JPEGs — used by train_loop.py's multimodal vision critic to inspect
    visual composition (facecam position, gameplay visibility, black bars, glitches).

    Best-effort: if a given timestamp's frame can't be extracted, it's skipped rather than
    failing the whole batch — the caller (train_loop.py) degrades to text-only evaluation
    if too few frames come back."""
    try:
        duration = get_video_duration(video_path)
    except RuntimeError as e:
        logger.warning("Could not determine duration for %s, skipping frame extraction: %s", video_path, e)
        return []

    frames_dir.mkdir(exist_ok=True)
    video_mtime = video_path.stat().st_mtime

    def _extract_one(i: int, fraction: float) -> Optional[Path]:
        timestamp = max(0.0, duration * fraction)
        frame_path = frames_dir / f"{video_path.stem}_frame{i}.jpg"

        # Cache hit: a frame already exists AND is at least as new as the source video, so
        # it was extracted from this exact render, not a stale one — output filenames are
        # reused across cycles (ffmpeg -y overwrites the same clip_<index>_<slug>.mp4), so a
        # frame older than its video is from a *different* render under the same name and
        # must not be trusted. This is what lets a clip re-evaluated across cycles (e.g.
        # after a purge-threshold change, with no actual re-render) skip re-extraction
        # entirely instead of re-encoding frames from scratch every time.
        if frame_path.exists() and frame_path.stat().st_mtime >= video_mtime:
            return frame_path

        cmd = [
            "ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(frame_path),
        ]
        try:
            result = _run_ffmpeg(cmd, timeout=FFMPEG_FRAME_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Preview frame extraction at %.2fs from %s timed out after %ds",
                timestamp, video_path, FFMPEG_FRAME_TIMEOUT_SECONDS,
            )
            return None
        if result.returncode != 0 or not frame_path.exists():
            logger.warning(
                "Could not extract preview frame at %.2fs from %s: %s",
                timestamp, video_path, result.stderr[-500:] if result.stderr else "",
            )
            return None
        return frame_path

    # The 3 frame grabs read the same source video but write distinct output files and share
    # no mutable state — an independent, embarrassingly-parallel ffmpeg subprocess spawn each,
    # so running them concurrently is a safe, meaningful cut in wall-clock time per clip.
    with ThreadPoolExecutor(max_workers=len(PREVIEW_FRAME_FRACTIONS)) as pool:
        results = list(pool.map(lambda args: _extract_one(*args), enumerate(PREVIEW_FRAME_FRACTIONS)))

    return [p for p in results if p is not None]


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
        # TikTok's standard "reaction on top, content below" split: the facecam gets the
        # top third, the gameplay gets the bottom two-thirds — not an even 50/50 stack,
        # since the facecam is a reaction reference, not the main attraction.
        # Each zone is exactly ONE filter chain over ONE crop of [0:v] — no split/overlay
        # of two copies of the same source, which previously made the gameplay render as
        # a visually duplicated screen.
        face_x, face_y, face_w, face_h = facecam_box
        game_x, game_y, game_w, game_h = gameplay_box
        face_zone_h = int(output_h * SPLIT_SCREEN_FACE_RATIO)
        game_zone_h = output_h - face_zone_h

        # Facecam: "cover" fit — scale up with force_original_aspect_ratio=increase (so no
        # axis is ever stretched) and crop the overhang — fills its zone edge-to-edge with
        # no gaps, since a tight face crop rarely needs to show its full source frame.
        # format=yuv420p pins a consistent pixel format going into vstack — mixing formats
        # at a merge point is what produces the green/corrupted-color artifacts.
        face_filter = (
            f"[0:v]crop={face_w}:{face_h}:{face_x}:{face_y},"
            f"scale={output_w}:{face_zone_h}:force_original_aspect_ratio=increase,"
            f"crop={output_w}:{face_zone_h},setsar=1,format=yuv420p[face]"
        )

        # Gameplay: "contain" fit — scaled DOWN to fit inside the zone with the ENTIRE frame
        # preserved (force_original_aspect_ratio=decrease, never cropped), then padded with
        # black to exactly fill the zone. pad() bounds both width AND height in one step, so
        # the result is always exactly output_w x game_zone_h — appears exactly once.
        game_filter = (
            f"[0:v]crop={game_w}:{game_h}:{game_x}:{game_y},"
            f"scale={output_w}:{game_zone_h}:force_original_aspect_ratio=decrease,"
            f"pad={output_w}:{game_zone_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,format=yuv420p[game]"
        )

        return (
            f"{face_filter};"
            f"{game_filter};"
            f"[face][game]vstack=inputs=2,format=yuv420p[stacked];"
            f"[stacked]subtitles='{subtitles}'[outv]"
        )

    if layout == LAYOUT_FULL_CAM:
        # Single-face content where the whole source frame IS the subject (no separate
        # gameplay feed to show alongside it) — same "cover" crop technique as split_screen's
        # face zone above, just filling the ENTIRE canvas instead of only the top third.
        # facecam_box here was detected with FULL_CAM_PADDING_FACTOR, not the tighter default
        # used for split_screen's short zone — see that constant's own docstring.
        face_x, face_y, face_w, face_h = facecam_box
        return (
            f"[0:v]crop={face_w}:{face_h}:{face_x}:{face_y},"
            f"scale={output_w}:{output_h}:force_original_aspect_ratio=increase,"
            f"crop={output_w}:{output_h},setsar=1,format=yuv420p[cropped];"
            f"[cropped]subtitles='{subtitles}'[outv]"
        )

    if layout == LAYOUT_BLUR_BACKGROUND:
        # setsar=1 right at the source reference (not just on the outputs) so a source
        # with non-square sample-aspect-ratio metadata can never throw off the aspect math
        # below. Blur a heavily downscaled copy, then upscale — far cheaper than blurring
        # full-res.
        small_w, small_h = output_w // 4, output_h // 4
        return (
            f"[0:v]setsar=1,scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
            f"crop={small_w}:{small_h},boxblur=20:20,scale={output_w}:{output_h},"
            f"setsar=1,format=yuv420p[bg];"
            # Contain-fit within the FULL canvas — both width AND height are bounded here
            # (force_original_aspect_ratio=decrease against output_w:output_h), not just
            # width. The previous width-only scale (scale=W:-2) left height unconstrained,
            # so a less-widescreen source could produce a foreground TALLER than the
            # canvas, pushing the centering overlay negative and corrupting the composite.
            f"[0:v]setsar=1,scale={output_w}:{output_h}:force_original_aspect_ratio=decrease,"
            f"setsar=1,format=yuv420p[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[stacked];"
            f"[stacked]subtitles='{subtitles}'[outv]"
        )

    raise ValueError(f"Unknown layout: {layout}")


# Intel Quick Sync hardware H.264 encoder — decode/filtering (crop/scale/vstack/subtitles)
# still run on the CPU (subtitle burn-in has no QSV path), but the encode step, usually the
# single most expensive part of rendering 15-20 clips per video, runs on the iGPU instead.
# QSV has no CRF; -global_quality is its closest equivalent (ICQ mode, lower = better quality).
QSV_ENCODE_ARGS = ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "25"]
CPU_ENCODE_ARGS = ["-c:v", "libx264", "-preset", "veryfast"]


def _build_render_cmd(source_video: Path, start: float, end: float, filter_complex: str, encode_args: list, output_path: Path) -> list:
    return [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", str(source_video),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "0:a?",
        *encode_args,
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        str(output_path),
    ]


# A full clip render (decode + filter graph + encode) legitimately needs minutes; a single
# still-frame grab should take well under a second. These used to share one 600s timeout,
# so a hung ffmpeg during a trivial preview-frame extraction could stall an entire cycle for
# up to 30 minutes (3 frames x 10 minutes each) before the vision critic even got a chance
# to degrade to text-only.
FFMPEG_RENDER_TIMEOUT_SECONDS = 600
FFMPEG_FRAME_TIMEOUT_SECONDS = 30


def _run_ffmpeg(cmd: list, timeout: int = FFMPEG_RENDER_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    # encoding="utf-8" is required here: without it, subprocess.run's text mode falls back
    # to the system locale (cp1252/"charmap" on German Windows), which crashes with
    # UnicodeDecodeError the moment ffmpeg's UTF-8 log output contains a German umlaut.
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


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
    output_dir: Path | None = None,
) -> Path:
    # output_dir defaults to the shared OUTPUT_DIR (single-video/manual/Streamlit usage,
    # unchanged behavior) — auto_pilot.py passes a streamer-namespaced subdirectory instead
    # when running under orchestrator.py, so two streamers rendering concurrently can never
    # collide on the same clip_<index>_<slug>.mp4 path (found in review, 2026-08-18: H-14).
    if output_dir is None:
        output_dir = OUTPUT_DIR
    start = clip["start_time"]
    end = clip["end_time"]
    output_path = output_dir / clip_output_filename(index, clip["title"])

    filter_complex = build_filter_complex(layout, ass_path, output_w, output_h, facecam_box, gameplay_box)

    logger.info("Rendering clip %d (layout=%s) via h264_qsv -> %s", index, layout, output_path)
    cmd = _build_render_cmd(source_video, start, end, filter_complex, QSV_ENCODE_ARGS, output_path)
    result = _run_ffmpeg(cmd)

    if result.returncode != 0:
        # QSV can fail for reasons unrelated to our command (no compatible iGPU/driver on
        # this machine, session limits, etc.) — fall back to the CPU encoder rather than
        # losing the clip entirely.
        logger.warning(
            "h264_qsv failed for clip %d (exit code %d), falling back to libx264: %s",
            index, result.returncode, result.stderr[-2000:],
        )
        cmd = _build_render_cmd(source_video, start, end, filter_complex, CPU_ENCODE_ARGS, output_path)
        result = _run_ffmpeg(cmd)

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
    """Three-way auto-layout decision (2026-08-19, replacing a plain face-present/absent
    binary): zero or multiple faces both fall back to blur_background — zero because there's
    nothing to crop toward, multiple because a single static crop can't represent several
    people (guests present) without arbitrarily picking one. Exactly one face still needs a
    second signal to choose between full_cam and split_screen, since a face can be "one face"
    whether it's a close-up filling the whole frame or a small corner webcam box over a much
    bigger gameplay area — face_area_ratio (how much of the SOURCE frame the raw box covers)
    is that second signal."""
    if layout != LAYOUT_AUTO:
        return layout

    face_count, raw_box, frame_w, frame_h = vision.detect_faces_for_layout(str(video_path), clip_start)

    if face_count == 1:
        ratio = vision.face_area_ratio(raw_box, frame_w, frame_h)
        resolved = LAYOUT_FULL_CAM if ratio >= FULL_CAM_MIN_FACE_AREA_RATIO else LAYOUT_SPLIT_SCREEN
        logger.info(
            "Auto-layout at %.2fs: face_count=1, face_area_ratio=%.3f (threshold %.2f) -> %s",
            clip_start, ratio, FULL_CAM_MIN_FACE_AREA_RATIO, resolved,
        )
    else:
        resolved = LAYOUT_BLUR_BACKGROUND
        logger.info("Auto-layout at %.2fs: face_count=%d -> %s", clip_start, face_count, resolved)

    return resolved


def _write_clip_metadata_sidecar(output_path: Path, clip: dict) -> None:
    """A small .json next to each rendered clip recording exactly what it is — title,
    hook_explanation, viral_score, energy_rating, whatever else analyze.py/train_loop.py put
    on the clip dict, plus when it was rendered. Lets a reader (app.py's Clip Archiv) look up
    a clip's real metadata directly instead of re-deriving it from the filename's numeric
    index against "whichever *_clips.json happens to be newest" — that reverse-engineering
    breaks as soon as two cycles (or two concurrent streamers) both produce a clip_1_*.mp4
    (found in review, 2026-08-18: H-14). Best-effort: a failed write never fails the render
    it's describing."""
    try:
        atomic_io.atomic_write_json(output_path.with_suffix(".json"), {
            **clip,
            "rendered_at": datetime.now(timezone.utc).isoformat(),
        })
    except OSError as e:
        logger.warning("Could not write clip metadata sidecar for %s: %s", output_path, e)


def process_clips_iter(
    source_video: Path | None = None,
    layout: str = LAYOUT_SPLIT_SCREEN,
    video_format: str = DEFAULT_FORMAT,
    highlight_color: str = DEFAULT_HIGHLIGHT_COLOR,
    transcript: dict | None = None,
    output_dir: Path | None = None,
) -> Iterator[Tuple[int, int, dict, Path]]:
    """Render clips one at a time, yielding (position, total, clip, output_path) as each one
    finishes — lets a caller (e.g. the Streamlit UI) show real per-clip progress instead of
    blocking on the whole batch. `process()` below is a thin wrapper for callers that just
    want the final list. `transcript`, if given, overrides temp/transcription.json.
    `output_dir`, if given, overrides OUTPUT_DIR — see render_clip()'s docstring."""
    if layout not in SELECTABLE_LAYOUTS:
        raise ValueError(f"Unknown layout '{layout}', expected one of {SELECTABLE_LAYOUTS}")
    if video_format not in VIDEO_FORMATS:
        raise ValueError(f"Unknown video format '{video_format}', expected one of {tuple(VIDEO_FORMATS)}")

    if output_dir is None:
        output_dir = OUTPUT_DIR
    output_w, output_h = VIDEO_FORMATS[video_format]

    video_path = find_source_video(source_video)
    # Per-video file names (e.g. temp/my_video_clips.json) so re-running the pipeline on a
    # different source video doesn't read stale clips/transcript left over from another run.
    clips_path = TEMP_DIR / f"{video_path.stem}_clips.json"
    clips_data = load_json(clips_path)
    if transcript is None:
        transcription_path = TEMP_DIR / f"{video_path.stem}_transcription.json"
        transcript = load_json(transcription_path)

    clips = clips_data.get("clips", [])
    if not clips:
        raise RuntimeError(f"No clips found in {clips_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
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

    total = len(clips)
    for i, clip in enumerate(clips, start=1):
        effective_layout = resolve_layout(layout, video_path, clip["start_time"])

        facecam_box = None
        if effective_layout == LAYOUT_SPLIT_SCREEN:
            facecam_box = vision.get_facecam_coordinates(str(video_path), clip["start_time"])
        elif effective_layout == LAYOUT_FULL_CAM:
            facecam_box = vision.get_facecam_coordinates(
                str(video_path), clip["start_time"], padding_factor=FULL_CAM_PADDING_FACTOR,
            )

        ass_path = build_ass_for_clip(clip, transcript, i, highlight_color, output_w, output_h)
        output_path = render_clip(
            video_path, clip, ass_path, i, effective_layout, output_w, output_h, facecam_box, gameplay_box,
            output_dir=output_dir,
        )
        _write_clip_metadata_sidecar(output_path, clip)
        yield i, total, clip, output_path

    logger.info("Processing complete: %d clips rendered to %s", total, output_dir)


def process(
    source_video: Path | None = None,
    layout: str = LAYOUT_SPLIT_SCREEN,
    video_format: str = DEFAULT_FORMAT,
    highlight_color: str = DEFAULT_HIGHLIGHT_COLOR,
    transcript: dict | None = None,
) -> List[Path]:
    """Render all clips and return the output paths. See process_clips_iter() for a
    version that yields progress per clip instead of blocking until everything is done."""
    return [
        output_path
        for _, _, _, output_path in process_clips_iter(
            source_video, layout, video_format, highlight_color, transcript
        )
    ]


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
