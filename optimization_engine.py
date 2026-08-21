"""Continuous performance-feedback loop (2026-08-21): closes the loop from real, measured
view/like counts (viral_memory.json) back into future clip-selection/metadata decisions —
complementary to train_loop.py's critic, which judges content QUALITY via LLM opinion before
any real performance data exists. This module is purely quantitative: it groups past uploads
by a few tracked attributes (render layout, background-music track, hook_style, title
phrasing style) and tracks which values of each correlate with higher measured engagement,
then exposes that as (a) a soft prompt-bias analyze.py's selector can read, and (b) a
bandit-style bias for process.py's background-music picker.

IMPORTANT — what this can and can't measure: TikTok Studio's scraped page and YouTube's Data
API (videos.list) both expose views/likes only (see metrics_tracker.py). Retention/watch-time/
completion-rate require the separate YouTube Analytics API (a different OAuth scope,
yt-analytics.readonly, not currently requested) and have no TikTok equivalent in this
pipeline's scraping path at all. "Engagement rate" (likes/views) is used here as the real,
measurable proxy instead of fabricating a metric this pipeline doesn't actually collect.

Every bucket below requires MIN_SAMPLES_PER_BUCKET real measured uploads before it's trusted
for anything — at this project's current, very low upload volume, most/all buckets will
report "insufficient data" for a long while, which is the correct, honest behavior rather
than confidently overfitting a preference to 1-2 data points."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import atomic_io

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

VIRAL_MEMORY_PATH = Path("viral_memory.json")
OPTIMIZATION_STATE_PATH = Path("optimization_state.json")

# A bucket (one value of one tracked attribute, e.g. layout="full_cam") needs at least this
# many real measured uploads before its average performance is trusted for anything — below
# this, a single lucky/unlucky clip could look like a strong signal that isn't real.
MIN_SAMPLES_PER_BUCKET = 5

# How often run_daily_report() actually does real work when called repeatedly (e.g. from
# orchestrator.py's poll loop, every ~90s) — the function is cheap to call constantly; this
# just gates when it re-analyzes and re-logs, giving it a "daily run" cadence despite being
# invoked far more often than that.
REPORT_INTERVAL_HOURS = 24

# Attributes tracked per clip, provided the sidecar/viral_memory entry has them — older clips
# predating this feature simply don't contribute to that attribute's buckets.
TRACKED_ATTRIBUTES = ("layout", "music_track", "hook_style", "title_style")

# Subset of TRACKED_ATTRIBUTES the selector LLM can actually act on — hook_style is a field
# it writes directly, title_style is influenced indirectly via how it phrases the title.
# layout and music_track are decided entirely by process.py's own code (face-detection,
# pick_background_track()'s bandit) with zero input from the LLM's output, so a "preference"
# for them in the prompt would be inert instructions the model has no way to follow — found in
# review, 2026-08-21. Both are still tracked and reported in optimization_state.json (layout
# for human visibility, music_track to drive its own bandit bias) — just excluded from
# build_prompt_section()'s LLM-facing text specifically.
LLM_ACTIONABLE_ATTRIBUTES = ("hook_style", "title_style")

# Fraction of background-music picks that stay uniformly random even once a preferred track
# is known — keeps every track accumulating samples instead of the picker locking onto
# whichever one got lucky first (classic explore/exploit tradeoff for a bandit-style picker).
MUSIC_EXPLORATION_RATE = 0.4


def classify_title_style(title: str) -> str:
    """Cheap, deterministic heuristic — no LLM call. Buckets a title into one of a few
    surface-level phrasing styles so performance can be compared across styles without
    needing the LLM to self-report one (titles for OLD clips can still be classified
    retroactively this way, unlike an LLM-reported field like hook_style)."""
    stripped = (title or "").strip()
    if not stripped:
        return "unknown"
    if stripped.endswith("?"):
        return "question"
    if stripped[0].isdigit():
        return "list_number"
    if "!" in stripped:
        return "exclamation"
    return "statement"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return {}


def load_viral_memory() -> dict:
    return _load_json(VIRAL_MEMORY_PATH)


def load_optimization_state() -> dict:
    return _load_json(OPTIMIZATION_STATE_PATH)


def _engagement_rate(views: int, likes: int) -> Optional[float]:
    if not views:
        return None
    return likes / views


def _measured_entries(memory: dict) -> List[dict]:
    """Entries with at least one real metrics fetch (checked_at set, and combined view count
    > 0) — excludes freshly-uploaded clips nothing has measured yet. Combines TikTok +
    YouTube views/likes into one proxy rather than analyzing the two platforms separately —
    a deliberate v1 simplification (see module docstring); revisit if it turns out
    misleading once real per-platform volume exists to check that against."""
    out = []
    for entry in memory.values():
        if not entry.get("checked_at"):
            continue
        views = (entry.get("tiktok_views") or 0) + (entry.get("youtube_views") or 0)
        if views <= 0:
            continue
        likes = (entry.get("tiktok_likes") or 0) + (entry.get("youtube_likes") or 0)
        out.append({**entry, "_views": views, "_likes": likes, "_engagement": _engagement_rate(views, likes)})
    return out


def analyze_performance(memory: dict) -> dict:
    """Pure function: groups measured uploads by each attribute in TRACKED_ATTRIBUTES,
    computes per-bucket sample size / average views / average engagement rate, and picks a
    'preferred' value per attribute among buckets that clear MIN_SAMPLES_PER_BUCKET. Returns
    a dict ready to serialize as optimization_state.json's "attributes"/"preferred"/
    "summary_lines" — see module docstring for what "engagement rate" does and doesn't
    capture."""
    entries = _measured_entries(memory)

    attributes: Dict[str, dict] = {}
    preferred: Dict[str, str] = {}
    summary_lines: List[str] = []
    llm_summary_lines: List[str] = []
    insufficient: List[str] = []

    for attr in TRACKED_ATTRIBUTES:
        buckets: Dict[str, List[dict]] = {}
        for e in entries:
            value = e.get(attr)
            if not value:
                continue
            buckets.setdefault(value, []).append(e)

        bucket_stats = {}
        for value, rows in buckets.items():
            n = len(rows)
            avg_views = sum(r["_views"] for r in rows) / n
            engagement_rows = [r["_engagement"] for r in rows if r["_engagement"] is not None]
            avg_engagement = sum(engagement_rows) / len(engagement_rows) if engagement_rows else None
            bucket_stats[value] = {
                "n": n,
                "avg_views": round(avg_views, 1),
                "avg_engagement_rate": round(avg_engagement, 4) if avg_engagement is not None else None,
            }
        attributes[attr] = bucket_stats

        eligible = {
            v: s for v, s in bucket_stats.items()
            if s["n"] >= MIN_SAMPLES_PER_BUCKET and s["avg_engagement_rate"] is not None
        }
        if eligible:
            best_value = max(eligible, key=lambda v: eligible[v]["avg_engagement_rate"])
            preferred[attr] = best_value
            baseline = sum(s["avg_engagement_rate"] for s in eligible.values()) / len(eligible)
            line = (
                f"{attr}={best_value} (n={eligible[best_value]['n']}, "
                f"{eligible[best_value]['avg_engagement_rate'] * 100:.1f}% engagement vs "
                f"{baseline * 100:.1f}% avg across measured {attr} values)"
            )
            summary_lines.append(line)
            if attr in LLM_ACTIONABLE_ATTRIBUTES:
                llm_summary_lines.append(line)
        else:
            insufficient.append(attr)

    return {
        "sample_size_total": len(entries),
        "attributes": attributes,
        "preferred": preferred,
        "summary_lines": summary_lines,
        "llm_summary_lines": llm_summary_lines,
        "insufficient_data_for": insufficient,
    }


def build_prompt_section(state: dict) -> str:
    """Soft, secondary bias for analyze.py's selector prompt — deliberately worded as
    preference, not constraint (unlike ai_guidelines.txt's PENALTIES), since this is derived
    from real but often still small-sample performance data that can easily be noise at this
    project's current upload volume.

    Only LLM_ACTIONABLE_ATTRIBUTES show up here, not the full summary_lines — layout and
    music_track are decided entirely by process.py's own code, not the LLM's output, so
    telling the model to "lean toward" one would be an instruction it has no way to follow."""
    summary_lines = state.get("llm_summary_lines") or []
    if not summary_lines:
        return ""

    lines = "\n".join(f"- {line}" for line in summary_lines)
    return (
        "\n\nPERFORMANCE-LEARNED PREFERENCES (from real measured view/like counts on "
        f"{state.get('sample_size_total', 0)} past upload(s) — views and likes/views "
        "engagement rate only; retention/watch-time/completion-rate are not tracked by this "
        "pipeline, see optimization_engine.py):\n" + lines +
        "\n\nThese are SOFT, secondary preferences, not hard rules — when multiple candidate "
        "clips otherwise clear the quality bar equally well, lean toward the styles above, "
        "but never let this override SINNHAFTIGKEIT or the core quality bar."
    )


def preferred_music_track() -> Optional[str]:
    state = load_optimization_state()
    return (state.get("preferred") or {}).get("music_track")


def write_optimization_state(state: dict) -> None:
    atomic_io.atomic_write_json(OPTIMIZATION_STATE_PATH, state)


def run_daily_report(force: bool = False) -> Optional[dict]:
    """Self-gated to roughly once per REPORT_INTERVAL_HOURS regardless of how often it's
    called — orchestrator.py calls this on every poll iteration (its main loop is the only
    always-on loop in this pipeline; auto_pilot.py only runs while a streamer is live), which
    is far more often than daily, so this function needs to be cheap and safe to call
    constantly. Returns the new state on a real run, None when skipped as too soon.
    force=True (e.g. a manual/CLI run) always re-analyzes regardless of the last report."""
    existing = load_optimization_state()
    last_report_at = existing.get("last_report_at")
    if not force and last_report_at:
        try:
            last = datetime.fromisoformat(last_report_at)
            if (datetime.now(timezone.utc) - last).total_seconds() < REPORT_INTERVAL_HOURS * 3600:
                return None
        except ValueError:
            pass  # corrupt timestamp — treat as due for a fresh report rather than getting stuck

    memory = load_viral_memory()
    result = analyze_performance(memory)
    now = datetime.now(timezone.utc).isoformat()
    result["generated_at"] = now
    result["last_report_at"] = now
    write_optimization_state(result)

    if result["summary_lines"]:
        logger.info(
            "📈 Optimization report: %d measured upload(s) — %s",
            result["sample_size_total"], "; ".join(result["summary_lines"]),
        )
    else:
        logger.info(
            "📈 Optimization report: %d measured upload(s) — not enough data yet for any "
            "tracked attribute (need >= %d samples per bucket); tracked so far: %s",
            result["sample_size_total"], MIN_SAMPLES_PER_BUCKET,
            ", ".join(result["insufficient_data_for"]) or "none",
        )
    return result
