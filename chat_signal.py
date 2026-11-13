"""Twitch chat activity as an extra clip-selection signal (prepared, not yet wired to a
real chat capture).

A chat log is reduced to message timestamps (seconds from the start of the VOD/recording).
Windows whose message rate is a statistical outlier become "chat spikes" and can be listed in
the analysis prompt next to the audio energy spikes (analyze.py).
"""

import json
import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

CHAT_WINDOW_SECONDS = 5.0
CHAT_SPIKE_THRESHOLD_STD = 2.0   # same idea as analyze.ENERGY_SPIKE_THRESHOLD_STD
CHAT_MIN_MESSAGES = 5            # a "spike" of 2 messages in a quiet chat is noise
MAX_SPIKES = 15                  # keep the prompt section short

_CLOCK_RE = re.compile(r"^\[?(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)\]?")


def parse_chat_log(path: Path) -> List[float]:
    """Message timestamps in seconds from a log file. Accepts JSON lines ({"t": 12.3} or
    {"timestamp": 12.3}), a JSON list of numbers/objects, or text lines starting with
    [HH:MM:SS], [MM:SS] or a plain number of seconds. Unreadable lines are skipped."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read chat log %s: %s", path, e)
        return []

    stamps: List[float] = []
    stripped = raw.lstrip()
    if stripped.startswith("["):
        try:
            for item in json.loads(raw):
                ts = _timestamp_of(item)
                if ts is not None:
                    stamps.append(ts)
            return sorted(stamps)
        except ValueError:
            pass  # not a JSON list, e.g. "[00:01:02] user: hi" lines

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        ts = None
        if line.startswith("{"):
            try:
                ts = _timestamp_of(json.loads(line))
            except ValueError:
                ts = None
        else:
            ts = _timestamp_of(line)
        if ts is not None:
            stamps.append(ts)
    return sorted(stamps)


def _timestamp_of(item) -> Optional[float]:
    if isinstance(item, bool):
        return None
    if isinstance(item, (int, float)):
        return float(item) if item >= 0 else None
    if isinstance(item, dict):
        for key in ("t", "timestamp", "offset", "seconds"):
            if key in item:
                return _timestamp_of(item[key])
        return None
    if isinstance(item, str):
        text = item.strip()
        m = _CLOCK_RE.match(text)
        if m and (":" in text.split()[0]):
            h, mnt, sec = m.groups()
            return int(h or 0) * 3600 + int(mnt) * 60 + float(sec)
        try:
            value = float(text.split()[0].strip("[]"))
        except (ValueError, IndexError):
            return None
        return value if value >= 0 else None
    return None


def chat_rate_windows(timestamps: Iterable[float], window_seconds: float = CHAT_WINDOW_SECONDS) -> List[tuple]:
    """[(window_start_seconds, message_count)] covering first..last message, empty windows included."""
    ts = np.array(sorted(t for t in timestamps if t >= 0), dtype=float)
    if ts.size == 0:
        return []
    n_windows = int(ts[-1] // window_seconds) + 1
    counts = np.bincount((ts // window_seconds).astype(int), minlength=n_windows)
    return [(i * window_seconds, int(c)) for i, c in enumerate(counts)]


def find_chat_spikes(
    timestamps: Iterable[float],
    window_seconds: float = CHAT_WINDOW_SECONDS,
    threshold_std: float = CHAT_SPIKE_THRESHOLD_STD,
    min_messages: int = CHAT_MIN_MESSAGES,
    max_spikes: int = MAX_SPIKES,
) -> List[dict]:
    """Outlier windows in message rate: [{"timestamp", "messages"}], strongest first, then
    returned in time order. Consecutive spike windows are merged into one (keeping the peak)."""
    windows = chat_rate_windows(timestamps, window_seconds)
    if len(windows) < 3:
        return []
    counts = np.array([c for _, c in windows], dtype=float)
    std = float(counts.std())
    if std == 0:
        return []
    threshold = max(float(counts.mean()) + threshold_std * std, min_messages - 1)
    peaks: List[dict] = []
    for (start, count) in windows:
        if count <= threshold:
            continue
        if peaks and start - peaks[-1]["_end"] <= 0:
            if count > peaks[-1]["messages"]:
                peaks[-1].update(timestamp=round(start, 1), messages=count)
            peaks[-1]["_end"] = start + window_seconds
        else:
            peaks.append({"timestamp": round(start, 1), "messages": count, "_end": start + window_seconds})
    strongest = sorted(peaks, key=lambda p: -p["messages"])[:max_spikes]
    result = [{"timestamp": p["timestamp"], "messages": p["messages"]} for p in sorted(strongest, key=lambda p: p["timestamp"])]
    logger.info("Detected %d chat spike(s) in %d window(s)", len(result), len(windows))
    return result


def build_chat_prompt_section(spikes: List[dict]) -> str:
    """Optional prompt block, analogous to analyze.build_energy_prompt_section()."""
    if not spikes:
        return ""
    lines = [f"[{_fmt(s['timestamp'])}] CHAT SPIKE ({s['messages']} messages in {CHAT_WINDOW_SECONDS:.0f}s)" for s in spikes]
    return (
        "\n\nTWITCH CHAT ACTIVITY SPIKES (moments where viewers suddenly flooded the chat — "
        "a sign something notable just happened; the reaction usually follows the event by a few "
        "seconds, so look slightly BEFORE each timestamp in the transcript above; use only as a "
        "hint, never as a reason to pick a moment that is not in the transcript):\n" + "\n".join(lines)
    )


def _fmt(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)  # same format as analyze.format_timestamp
    return f"{int(minutes):02d}:{secs:04.1f}"
