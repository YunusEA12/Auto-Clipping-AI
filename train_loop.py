"""Training loop: a multimodal "Critic" LLM independently re-scores clips a previous
analyze.py run already picked, from -10 (bad) to +10 (great) — informed by the transcript
context around each clip, any user feedback saved via the Streamlit UI, 3 extracted preview
frames per clip (start/middle/end) for visual evaluation, and real TikTok view/like data
from viral_memory.json (see metrics_tracker.py) when available.

The critic can produce four kinds of rules, which accumulate into three sections of
ai_guidelines.txt:
  - avoid_rule / positive_rule           -> "Text/Content" section (narrative, hook, pacing)
  - visual_avoid_rule / visual_positive_rule -> "Visual/Layout" section (facecam position,
    gameplay visibility, black bars, glitches)
  - viral_pattern_rule                   -> "Viral Success Patterns" section (topics/energy/
    styles that measurably performed well or flopped on real TikTok metrics)

analyze.py injects the whole file into its own system prompt on every future run (see
load_ai_guidelines_section() in analyze.py) — closing the loop so future clip selection
learns from past narrative mistakes, visual composition bugs, AND what actually went viral.

If the vision API call fails for any reason (frame extraction failure, encoding failure,
API error), this degrades to a text-only critic pass instead of raising — see run_critic().

Usage:
    python train_loop.py                       # evaluates the most recently written *_clips.json
    python train_loop.py --clips temp/foo_clips.json
"""

import argparse
import base64
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

import analyze
import process as process_module

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
VIRAL_MEMORY_PATH = Path("viral_memory.json")

# Text-only fallback model (cheap/fast) vs. the vision-capable model used when preview
# frames are available. Falls back to MODEL automatically if the vision path fails.
MODEL = "gpt-4o-mini"
VISION_MODEL = "gpt-4o"
MAX_COMPLETION_TOKENS = 4096

# reward_score >= this threshold is eligible to contribute a "DO" rule (not every positive
# clip is exceptional enough to generalize into a standing rule).
POSITIVE_RULE_THRESHOLD = 7

CRITIC_SYSTEM_PROMPT = """You are a ruthless TikTok/Shorts content critic reviewing clips
that an upstream AI already selected from a longer video. For each clip you're given its
title, hook explanation, the actual transcript text spoken during its time range, any human
feedback already recorded about it, and — when available — 3 preview screenshots (near-
start, middle, near-end) plus real TikTok performance data for previously uploaded clips.
Score honestly — you are not the one who picked it, so you have no reason to defend a bad
choice.

Evaluate TWO independent dimensions:

1. NARRATIVE: Is the transcript text coherent and engaging? Does it have a clear beginning,
   understandable context, and a resolved end? Does it start or end mid-sentence? Is there
   an actual hook, or is it boring/rambling without a payoff?

2. VISUAL COMPOSITION (only when screenshots are provided): Is the split-screen layout
   correct — facecam centered in the top third, gameplay clearly visible filling the bottom
   two-thirds? Are there visual glitches, duplicated footage, unexpected black bars, a
   misplaced or cropped-wrong facecam, or anything else visually broken? Judge purely from
   what's visible in the screenshots, not from the transcript.

Score each clip's overall reward_score from -10 to +10, weighing both dimensions:
- Negative scores (-10 to -1): bad on either dimension — boring/flat with no payoff,
  mid-sentence cuts, no hook, confusing context, OR broken visual composition (wrong facecam
  position, black bars, glitches, duplicated content).
- Positive scores (+1 to +10): solid on both dimensions — coherent narrative with a real
  hook AND (when screenshots are available) correct, clean visual composition.

Rules for the four kinds of rules you can produce:
- If reward_score is negative because of the NARRATIVE, you MUST set avoid_rule: a short,
  general, reusable rule preventing this specific narrative failure mode (e.g. "Schneide
  niemals Clips, die mit einem unvollständigen Satz enden.").
- If reward_score is 7+ because of the NARRATIVE, optionally set positive_rule describing
  what worked narratively.
- If screenshots reveal a VISUAL problem (regardless of the narrative score), set
  visual_avoid_rule: a short, general, reusable rule about the visual composition issue
  (e.g. "Decrease face crop zoom — facecam is cut off at the edges." or "Erhöhe den
  Blur-Radius im Gameplay-Hintergrund, es wirken sichtbare Kanten.").
- If screenshots show excellent visual composition worth repeating, optionally set
  visual_positive_rule.
- If you're shown VIRAL PERFORMANCE DATA (real view/like counts for previously uploaded
  clips) and this clip's topic, energy level, or visual style resembles a pattern in that
  data, set viral_pattern_rule: if a similar style previously performed well, phrase a
  POSITIVE pattern to replicate it; if it flopped, phrase a PENALTY to avoid it. Leave this
  empty if there's no viral data or no clear pattern to draw from.

Be concrete about every rule ("Clip war schlecht" is not acceptable). Write all rule text in
German, matching the style of any existing guidelines you're shown as examples.
"""


class ClipVerdict(BaseModel):
    clip_title: str = Field(description="The title of the clip being scored, copied exactly")
    reward_score: int = Field(description="Overall reward score from -10 (bad) to +10 (great)", ge=-10, le=10)
    reasoning: str = Field(description="Brief explanation of the score, covering narrative and (if judged) visual composition")
    avoid_rule: Optional[str] = Field(
        default=None,
        description="Required if reward_score is negative due to narrative issues: a general, reusable rule to avoid this mistake",
    )
    positive_rule: Optional[str] = Field(
        default=None,
        description="Optional if reward_score >= 7 due to narrative strengths: a general, reusable rule describing what worked",
    )
    visual_avoid_rule: Optional[str] = Field(
        default=None,
        description="Set if screenshots reveal a visual composition problem (wrong facecam position, black bars, glitches, duplication)",
    )
    visual_positive_rule: Optional[str] = Field(
        default=None,
        description="Set if screenshots show excellent visual composition worth repeating",
    )
    viral_pattern_rule: Optional[str] = Field(
        default=None,
        description="Set only when viral performance data is available and this clip matches a real winning/losing pattern",
    )


class CriticBatch(BaseModel):
    verdicts: List[ClipVerdict]


def find_latest_clips_path() -> Optional[Path]:
    candidates = sorted(TEMP_DIR.glob("*_clips.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def load_feedback_by_title() -> Dict[str, List[str]]:
    """Map clip_title -> [feedback texts], from the same feedback.json the Streamlit UI's
    "Feedback speichern" button writes to — this is real human-labeled negative signal
    already being collected, reused here instead of building a second collection path."""
    if not analyze.FEEDBACK_PATH.exists():
        return {}

    try:
        with open(analyze.FEEDBACK_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s, evaluating without feedback: %s", analyze.FEEDBACK_PATH, e)
        return {}

    by_title: Dict[str, List[str]] = {}
    for entry in entries:
        title = entry.get("clip_title", "")
        feedback = entry.get("feedback", "")
        if title and feedback:
            by_title.setdefault(title, []).append(feedback)
    return by_title


def load_viral_memory_section() -> str:
    """Real TikTok view/like data for previously uploaded clips (written by
    metrics_tracker.py). Only clips that have actually been checked (views is not None)
    are included — freshly uploaded, not-yet-measured clips would just be noise here."""
    if not VIRAL_MEMORY_PATH.exists():
        return ""

    try:
        with open(VIRAL_MEMORY_PATH, "r", encoding="utf-8") as f:
            memory = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s, evaluating without viral data: %s", VIRAL_MEMORY_PATH, e)
        return ""

    if not isinstance(memory, dict):
        return ""

    scored = [e for e in memory.values() if isinstance(e, dict) and e.get("views") is not None]
    if not scored:
        return ""

    lines = [
        f"- \"{e.get('title', '?')}\" (viral_score={e.get('viral_score', '?')}, "
        f"energy={e.get('energy_rating', '?')}): {e.get('views', 0)} views, {e.get('likes', 0)} likes"
        for e in scored
    ]
    logger.info("Loaded %d viral performance data point(s) from %s", len(lines), VIRAL_MEMORY_PATH)
    return (
        "\n\nVIRAL PERFORMANCE DATA (real TikTok metrics for previously uploaded clips — "
        "cross-reference this with the clips you're scoring now; a similar topic/energy/"
        "visual style to a past winner should push reward_score up and may earn a "
        "viral_pattern_rule, a similar style to a past flop should push it down):\n"
        + "\n".join(lines)
    )


def extract_clip_transcript_text(transcript: Optional[dict], start: float, end: float) -> str:
    """The actual words spoken during a clip's time range, so the critic can judge
    mid-sentence cuts and missing hooks against the real transcript instead of guessing
    from the title/hook_explanation alone."""
    if not transcript:
        return "(Transkript nicht verfügbar)"

    segments = transcript.get("segments", [])
    overlapping = [seg["text"] for seg in segments if seg["end"] > start and seg["start"] < end]
    text = " ".join(overlapping).strip()
    return text or "(kein Transkripttext in diesem Zeitbereich gefunden)"


def _encode_image_data_uri(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
    except OSError as e:
        logger.warning("Could not read frame %s for vision critic: %s", path, e)
        return None
    return f"data:image/jpeg;base64,{base64.b64encode(raw).decode('utf-8')}"


def extract_frames_for_clips(clips: List[dict], rendered: Optional[Dict[str, Path]]) -> Dict[str, List[Path]]:
    """Best-effort frame extraction for every clip that has a known rendered .mp4 path.
    `rendered` (clip_title -> output_path) is normally supplied by auto_pilot.py, which
    just rendered these clips; when called standalone (train_loop.py's CLI), falls back to
    matching output/clip_<index>_*.mp4 by position within the clips list. Returns {} — not
    an exception — if nothing could be extracted, so the caller can degrade to text-only."""
    frames_by_title: Dict[str, List[Path]] = {}

    for i, clip in enumerate(clips, start=1):
        title = clip.get("title", "")
        output_path = (rendered or {}).get(title)

        if output_path is None:
            candidates = sorted(process_module.OUTPUT_DIR.glob(f"clip_{i}_*.mp4"))
            output_path = candidates[0] if candidates else None

        if output_path is None or not Path(output_path).exists():
            continue

        frames = process_module.extract_preview_frames(Path(output_path))
        if frames:
            frames_by_title[title] = frames

    return frames_by_title


def build_critic_user_content(
    clips: List[dict],
    transcript: Optional[dict],
    feedback_by_title: Dict[str, List[str]],
    frames_by_title: Dict[str, List[Path]],
) -> List[dict]:
    """Builds the multimodal message content: a list of {"type": "text"|"image_url", ...}
    blocks, interleaving each clip's text context with its preview frames (if any) so the
    model can visually attribute images to the right clip."""
    content: List[dict] = [{"type": "text", "text": "Bewerte die folgenden Clips:"}]

    for idx, clip in enumerate(clips, start=1):
        title = clip.get("title", "Untitled")
        feedback = feedback_by_title.get(title)
        block = (
            f"\n--- Clip {idx}: {title} ---\n"
            f"Hook-Begründung (vom Auswahl-Modell): {clip.get('hook_explanation', '')}\n"
            f"Transkript im Clip-Zeitbereich: "
            f"{extract_clip_transcript_text(transcript, clip['start_time'], clip['end_time'])}"
        )
        if feedback:
            block += "\nMenschliches Feedback zu diesem Clip: " + " | ".join(feedback)
        content.append({"type": "text", "text": block})

        for frame_path in frames_by_title.get(title, []):
            data_uri = _encode_image_data_uri(frame_path)
            if data_uri:
                content.append({"type": "image_url", "image_url": {"url": data_uri}})

    return content


def _call_critic(content: List[dict], model: str) -> CriticBatch:
    client = OpenAI()
    viral_section = load_viral_memory_section()
    if viral_section:
        content = content + [{"type": "text", "text": viral_section}]

    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format=CriticBatch,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )

    choice = completion.choices[0]
    parsed = choice.message.parsed
    if parsed is None:
        logger.warning(
            "Critic response could not be parsed (finish_reason=%s) — no guidelines will be "
            "derived from this run.", choice.finish_reason,
        )
        return CriticBatch(verdicts=[])

    return parsed


def run_critic(
    clips: List[dict],
    transcript: Optional[dict],
    feedback_by_title: Dict[str, List[str]],
    rendered: Optional[Dict[str, Path]] = None,
    vision_model: str = VISION_MODEL,
    text_model: str = MODEL,
) -> CriticBatch:
    """Tries a multimodal (text + preview frames) critic pass first; on ANY failure —
    frame extraction, image encoding, or the vision API call itself — falls back to a
    text-only pass instead of raising, so a vision outage never crashes auto_pilot.py."""
    try:
        frames_by_title = extract_frames_for_clips(clips, rendered)
        if frames_by_title:
            content = build_critic_user_content(clips, transcript, feedback_by_title, frames_by_title)
            logger.info(
                "Sending %d clip(s) with %d preview frame set(s) to the vision critic (%s)",
                len(clips), len(frames_by_title), vision_model,
            )
            return _call_critic(content, vision_model)
        logger.warning("No preview frames available — falling back to text-only critic evaluation")
    except Exception as e:
        logger.warning("Vision critic failed (%s) — falling back to text-only evaluation", e)

    content = build_critic_user_content(clips, transcript, feedback_by_title, {})
    logger.info("Sending %d clip(s) to the text-only critic (%s)", len(clips), text_model)
    try:
        return _call_critic(content, text_model)
    except Exception as e:
        logger.error("Critic LLM call failed (text-only fallback): %s", e)
        raise


# --- ai_guidelines.txt: three categorized sections ------------------------------------------

CATEGORY_HEADERS = {
    "content_positive": "[+] POSITIVE REWARDS (DO THIS):",
    "content_negative": "[-] PENALTIES (NEVER DO THIS):",
    "visual_positive": "[VISUAL+] VISUAL COMPOSITION — DO THIS:",
    "visual_negative": "[VISUAL-] VISUAL COMPOSITION — NEVER DO THIS:",
    "viral_patterns": "[VIRAL] SUCCESS PATTERNS (FROM PERFORMANCE DATA):",
}
CATEGORY_ORDER = ["content_positive", "content_negative", "visual_positive", "visual_negative", "viral_patterns"]


def parse_guidelines_file(content: str) -> Dict[str, List[str]]:
    """Public so app.py's "KI Gehirn" tab can reuse the exact same parsing logic instead of
    re-implementing it to render the guidelines file in the dashboard."""
    result: Dict[str, List[str]] = {key: [] for key in CATEGORY_ORDER}
    header_to_key = {header: key for key, header in CATEGORY_HEADERS.items()}
    current: Optional[str] = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in header_to_key:
            current = header_to_key[stripped]
            continue
        if stripped.startswith("- ") and current is not None:
            result[current].append(stripped[2:].strip())

    return result


def load_existing_guidelines() -> Dict[str, List[str]]:
    if not analyze.AI_GUIDELINES_PATH.exists():
        return {key: [] for key in CATEGORY_ORDER}
    try:
        content = analyze.AI_GUIDELINES_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read existing %s, starting fresh: %s", analyze.AI_GUIDELINES_PATH, e)
        return {key: [] for key in CATEGORY_ORDER}
    return parse_guidelines_file(content)


def _dedupe(rules: List[str]) -> List[str]:
    seen = set()
    result = []
    for rule in rules:
        rule = rule.strip()
        if rule and rule not in seen:
            seen.add(rule)
            result.append(rule)
    return result


def save_guidelines(categories: Dict[str, List[str]]) -> None:
    lines: List[str] = []
    for key in CATEGORY_ORDER:
        lines.append(CATEGORY_HEADERS[key])
        rules = categories.get(key) or []
        lines += [f"- {rule}" for rule in rules] if rules else ["- (noch keine)"]
        lines.append("")

    analyze.AI_GUIDELINES_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info(
        "Saved guidelines to %s: %s",
        analyze.AI_GUIDELINES_PATH, {key: len(categories.get(key) or []) for key in CATEGORY_ORDER},
    )


def run_training_loop(
    clips_path: Optional[Path] = None,
    model: str = MODEL,
    rendered: Optional[Dict[str, Path]] = None,
) -> Tuple[Path, CriticBatch]:
    """Run the critic over a batch of clips, update ai_guidelines.txt, and return
    (guidelines_path, batch) — `batch` (the per-clip reward_score verdicts) is what lets a
    caller like auto_pilot.py decide which clips to purge, without re-running the critic.

    `rendered` (clip_title -> rendered .mp4 path), when supplied by auto_pilot.py, lets the
    critic extract preview frames for visual evaluation; without it, this falls back to
    matching output/clip_<n>_*.mp4 by position, or to text-only if neither works.
    """
    load_dotenv()

    if clips_path is None:
        clips_path = find_latest_clips_path()
    if clips_path is None or not clips_path.exists():
        raise FileNotFoundError(f"No *_clips.json file found to evaluate: {clips_path}")

    with open(clips_path, "r", encoding="utf-8") as f:
        clips_data = json.load(f)
    clips = clips_data.get("clips", [])
    if not clips:
        raise RuntimeError(f"No clips found in {clips_path}")

    transcription_path = analyze.transcription_path_for(clips_path)
    transcript = analyze.load_transcript(transcription_path) if transcription_path.exists() else None
    if transcript is None:
        logger.warning(
            "No matching transcript found at %s; critic will judge without transcript context",
            transcription_path,
        )

    feedback_by_title = load_feedback_by_title()

    batch = run_critic(clips, transcript, feedback_by_title, rendered, VISION_MODEL, model)

    new_content_positive, new_content_negative = [], []
    new_visual_positive, new_visual_negative = [], []
    new_viral_patterns = []

    for verdict in batch.verdicts:
        logger.info(
            "Critic verdict: '%s' -> reward_score=%d (%s)",
            verdict.clip_title, verdict.reward_score, verdict.reasoning,
        )
        if verdict.reward_score < 0:
            if verdict.avoid_rule:
                new_content_negative.append(verdict.avoid_rule)
            else:
                logger.warning(
                    "Clip '%s' got a negative score (%d) but no avoid_rule was provided",
                    verdict.clip_title, verdict.reward_score,
                )
        elif verdict.reward_score >= POSITIVE_RULE_THRESHOLD and verdict.positive_rule:
            new_content_positive.append(verdict.positive_rule)

        if verdict.visual_avoid_rule:
            new_visual_negative.append(verdict.visual_avoid_rule)
        if verdict.visual_positive_rule:
            new_visual_positive.append(verdict.visual_positive_rule)
        if verdict.viral_pattern_rule:
            new_viral_patterns.append(verdict.viral_pattern_rule)

    existing = load_existing_guidelines()
    merged = {
        "content_positive": _dedupe(existing["content_positive"] + new_content_positive),
        "content_negative": _dedupe(existing["content_negative"] + new_content_negative),
        "visual_positive": _dedupe(existing["visual_positive"] + new_visual_positive),
        "visual_negative": _dedupe(existing["visual_negative"] + new_visual_negative),
        "viral_patterns": _dedupe(existing["viral_patterns"] + new_viral_patterns),
    }
    save_guidelines(merged)

    return analyze.AI_GUIDELINES_PATH, batch


def main():
    parser = argparse.ArgumentParser(
        description="Run the multimodal critic training loop over the clips from a past analyze.py run."
    )
    parser.add_argument("--clips", type=Path, default=None, help="Path to a *_clips.json file (defaults to the newest one)")
    parser.add_argument("--model", default=MODEL, help="Text-only fallback critic model name")
    args = parser.parse_args()

    run_training_loop(args.clips, args.model)


if __name__ == "__main__":
    main()
