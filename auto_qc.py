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


def check_subtitles(path: Path) -> dict:
    """Validate generated ASS center anchors and conservative text extents in safe zones."""
    text = Path(path).read_text(encoding="utf-8-sig")
    width = int(re.search(r"PlayResX:\s*(\d+)", text)[1])
    height = int(re.search(r"PlayResY:\s*(\d+)", text)[1])
    styles = {}
    for line in text.splitlines():
        if line.startswith("Style:"):
            fields = line.split(":", 1)[1].strip().split(",")
            styles[fields[0]] = (float(fields[2]), float(fields[16]))
    count = 0
    violations = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            raise ValueError("Malformed ASS dialogue")
        style, body = fields[3], fields[9]
        font, outline = styles[style]
        position = re.search(r"\\pos\(([\d.]+),([\d.]+)\)", body)
        if position is None or "\\an5" not in body:
            raise ValueError("Unsupported subtitle positioning")
        x, y = map(float, position.groups())
        visible = re.sub(r"\{[^}]*\}", "", body)
        # Arial Black uppercase worst-case width ~1 em; deliberately conservative.
        lines = visible.split(r"\N")
        half_width = max(map(len, lines), default=0) * font * .5 + outline
        half_height = len(lines) * font * .65 + outline
        top = .05 if style == "TitleBox" else .10
        if x - half_width < width * .05 or x + half_width > width * .90 or y - half_height < height * top or y + half_height > height * .85:
            violations.append(count)
        count += 1
    return {"passed": count > 0 and not violations, "events": count,
            "violations": violations, "method": "conservative_ass_geometry"}


def check_render(path: Path, *, layout: str, subtitle_path: Path) -> dict:
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
        checks["subtitles_safezone"] = check_subtitles(subtitle_path)
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
