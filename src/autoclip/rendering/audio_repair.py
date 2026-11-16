"""Repair a rendered clip's loudness with measured two-pass normalization.

Only a validated temporary output replaces the render. The source recording is never
passed here. Video packets are copied, preserving layout and burned subtitles.
"""

import json
import math
import re
import subprocess
import tempfile
from pathlib import Path


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=300, check=True)
    return result.stderr


def _measure(path: Path) -> dict:
    log = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    matches = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', log, re.S)
    if not matches:
        raise ValueError("Missing loudness measurement")
    result = {key: float(value) for key, value in json.loads(matches[-1]).items() if key != "normalization_type"}
    if any(
        not math.isfinite(result[key]) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    ):
        raise ValueError("Audio is silent or not measurable")
    return result


def repair_render_loudness(path: Path) -> dict:
    path = Path(path)
    measured = _measure(path)
    if abs(measured["input_i"] + 14) <= 3:
        return {"changed": False, "before_lufs": measured["input_i"]}
    normalization = (
        "loudnorm=I=-14:TP=-1:LRA=11:linear=true:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}"
    )
    with tempfile.TemporaryDirectory(prefix=".audio-repair-", dir=path.parent) as directory:
        candidate = Path(directory) / path.name
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-c:v",
                "copy",
                "-af",
                normalization,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(candidate),
            ]
        )
        after = _measure(candidate)
        if abs(after["input_i"] + 14) > 3 or after["input_tp"] > 0:
            raise ValueError("Repaired audio failed loudness/peak verification")
        candidate.replace(path)
    return {
        "changed": True,
        "before_lufs": measured["input_i"],
        "after_lufs": after["input_i"],
        "method": "measured_two_pass",
    }
