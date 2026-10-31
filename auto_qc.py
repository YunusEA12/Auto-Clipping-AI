"""Deterministic CPU-only render checks. Unknown measurements fail closed.

Subtitle bounds are a conservative ASS geometry estimate, not OCR of burned pixels.
Thresholds are editorial safeguards, not a prediction of audience performance.
"""
import json
import logging
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import text_placement
import vision

logger = logging.getLogger(__name__)
SAMPLE_FRACTIONS = (.1, .3, .5, .7, .9)


def failed_result(reason: str) -> dict:
    return {"version": 1, "passed": False, "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": {}, "reasons": [reason]}


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise ValueError("Media measurement failed (exit %s)" % result.returncode)
    return result.stdout + "\n" + result.stderr


def measure_media(path: Path) -> dict:
    probe = json.loads(_run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))
    duration = float(probe["format"]["duration"])
    streams = probe.get("streams", [])
    if not math.isfinite(duration) or duration <= 0 or not any(s.get("codec_type") == "audio" for s in streams):
        raise ValueError("Missing duration or audio stream")
    log = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                "-vf", "blackdetect=d=0.5:pix_th=0.10:pic_th=0.98,freezedetect=n=-50dB:d=2",
                "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"])
    black = [float(v) for v in re.findall(r"black_duration:([\d.]+)", log)]
    starts = [float(v) for v in re.findall(r"freeze_start:\s*([\d.]+)", log)]
    ends = [float(v) for v in re.findall(r"freeze_end:\s*([\d.]+)", log)]
    freezes = [max(0., (ends[i] if i < len(ends) else duration) - start) for i, start in enumerate(starts)]
    matches = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', log, re.S)
    if not matches:
        raise ValueError("Missing loudnorm measurement")
    lufs = float(json.loads(matches[-1])["input_i"])
    if not math.isfinite(lufs):
        raise ValueError("Audio loudness is not measurable (silence)")
    return {"duration": duration, "lufs": lufs, "black_seconds": sum(black),
            "longest_freeze_seconds": max(freezes, default=0.)}


def check_subtitles(path: Path, face_rects: Optional[Sequence] = None) -> dict:
    """Validate generated ASS center anchors and conservative text extents in safe zones.

    Subtitles must also stay left of TikTok's right-hand button column
    (text_placement.UI_RIGHT_X_RATIO) and, when `face_rects` (canvas-pixel rectangles from
    text_placement.face_rects) are given, must not overlap any face."""
    text = Path(path).read_text(encoding="utf-8-sig")
    width = int(re.search(r"PlayResX:\s*(\d+)", text)[1])
    height = int(re.search(r"PlayResY:\s*(\d+)", text)[1])
    styles = {}
    for line in text.splitlines():
        if line.startswith("Style:"):
            fields = line.split(":", 1)[1].strip().split(",")
            styles[fields[0]] = (float(fields[2]), float(fields[16]), float(fields[19]), float(fields[20]))
    wrap_match = re.search(r"WrapStyle:\s*([0-3])", text)
    wrap_style = int(wrap_match[1]) if wrap_match else 0
    count = 0
    violations = []
    line_counts = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            raise ValueError("Malformed ASS dialogue")
        style, body = fields[3], fields[9]
        font, outline, margin_l, margin_r = styles[style]
        margin_l = float(fields[5]) or margin_l
        margin_r = float(fields[6]) or margin_r
        position = re.search(r"\\pos\(([\d.]+),([\d.]+)\)", body)
        if position is None or "\\an5" not in body:
            raise ValueError("Unsupported subtitle positioning")
        x, y = map(float, position.groups())
        visible = re.sub(r"\{[^}]*\}", "", body)
        # Arial Black uppercase average glyph advance is about .63 em. Libass
        # wraps at spaces within script/style margins, not at the safe-zone edge.
        # A long phrase is therefore not itself a horizontal overflow.
        em = font * .63
        available = width - margin_l - margin_r - 2 * outline
        override = re.search(r"\\q([0-3])", body)
        wrapping = int(override[1]) if override else wrap_style
        lines = 0
        widest = 0.
        too_wide = available <= 0
        paragraphs = re.split(r"\\N", visible)
        for paragraph in paragraphs:
            paragraph = paragraph.replace(r"\h", " ")
            if wrapping == 2:
                too_wide |= len(paragraph) * em > available
                widest = max(widest, len(paragraph) * em)
                lines += 1
                continue
            paragraph = paragraph.replace(r"\n", " ")
            used = 0.
            lines += 1
            for word in paragraph.split():
                word_width = len(word) * em
                too_wide |= word_width > available
                widest = max(widest, min(word_width, available))
                advance = word_width + (em if used else 0.)
                if used and used + advance > available:
                    lines += 1
                    used = word_width
                else:
                    used += advance
                widest = max(widest, min(used, available))
        # Center alignment: wrapped lines grow equally above/below the anchor.
        half_height = lines * font * .5 + outline
        top = .05 if style == "TitleBox" else .10
        if too_wide or not width * .05 <= x <= width * .90 or y - half_height < height * top or y + half_height > height * .85:
            violations.append(count)
        elif style != "TitleBox" and x + widest / 2 > width * text_placement.UI_RIGHT_X_RATIO + 1:
            violations.append(count)  # runs into the TikTok button column
        elif any(x - widest / 2 < x1 and x + widest / 2 > x0 and y - half_height < y1 and y + half_height > y0
                 for x0, y0, x1, y1 in (face_rects or ())):
            violations.append(count)  # covers a face
        line_counts.append(lines)
        count += 1
    return {"passed": count > 0 and not violations, "events": count,
            "violations": violations, "line_counts": line_counts, "method": "conservative_ass_geometry"}


def check_render(path: Path, *, layout: str, subtitle_path: Path, face_rects: Optional[Sequence] = None) -> dict:
    result = {"version": 1, "checked_at": datetime.now(timezone.utc).isoformat(), "checks": {}, "reasons": []}
    try:
        measured = measure_media(Path(path))
        checks = result["checks"]
        checks["black_frames"] = {"passed": measured["black_seconds"] < .5, "seconds": measured["black_seconds"]}
        checks["frozen_frames"] = {"passed": measured["longest_freeze_seconds"] < 2., "longest_seconds": measured["longest_freeze_seconds"]}
        checks["loudness"] = {"passed": abs(measured["lufs"] + 14.) <= 3., "integrated_lufs": measured["lufs"], "target_lufs": -14., "tolerance_lu": 3.}
        if layout == "split_screen":
            present = []
            for fraction in SAMPLE_FRACTIONS:
                count, box, _, height = vision.detect_faces_for_layout(str(path), measured["duration"] * fraction)
                present.append(bool(count and box and height > 0 and box[1] >= 0 and box[1] + box[3] <= height * .5))
            checks["upper_face"] = {"passed": sum(present) >= 4, "present": present}
        else:
            checks["upper_face"] = {"passed": True, "applicable": False, "reason": "Layout does not promise an upper facecam"}
        checks["subtitles_safezone"] = check_subtitles(subtitle_path, face_rects)
        result["reasons"] = [name for name, check in checks.items() if not check["passed"]]
        result["passed"] = not result["reasons"]
    except Exception as exc:
        result["passed"] = False
        result["reasons"].append("measurement_error: " + type(exc).__name__)
        logger.warning("Auto-QC measurement failed for %s: %s", path, type(exc).__name__)
    if not result["passed"]:
        logger.warning("Auto-QC blocks upload, retaining %s locally: %s", path, result["reasons"])
    return result


def metadata_blocked(data: dict) -> bool:
    # Old clips without auto_qc retain their existing critic contract. New QC must pass.
    return "auto_qc" in data and (not isinstance(data["auto_qc"], dict) or data["auto_qc"].get("passed") is not True)


def upload_blocked(path: Path) -> bool:
    try:
        data = json.loads(Path(path).with_suffix(".json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False  # historical caller without a render sidecar
    except (OSError, ValueError):
        return True
    return not isinstance(data, dict) or metadata_blocked(data)
