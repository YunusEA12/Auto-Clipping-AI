"""Training loop: a "Critic" LLM independently re-scores clips a previous analyze.py run
already picked, from -10 (bad) to +10 (great) — informed by the transcript context around
each clip and any user feedback saved via the Streamlit UI. Negative scores must come with
an explicit, reusable "AVOID" rule describing what went wrong; strong positive scores may
add a "DO" rule describing what worked. Both accumulate into ai_guidelines.txt, which
analyze.py injects into its own system prompt on every future run (see
load_ai_guidelines_section() in analyze.py) — closing the loop so future clip selection
learns from past mistakes and wins instead of repeating them.

Usage:
    python train_loop.py                       # evaluates the most recently written *_clips.json
    python train_loop.py --clips temp/foo_clips.json
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

import analyze

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
MODEL = "gpt-4o-mini"
MAX_COMPLETION_TOKENS = 4096

# reward_score >= this threshold is eligible to contribute a "DO" rule (not every positive
# clip is exceptional enough to generalize into a standing rule).
POSITIVE_RULE_THRESHOLD = 7

CRITIC_SYSTEM_PROMPT = """You are a ruthless TikTok/Shorts content critic reviewing clips
that an upstream AI already selected from a longer video. For each clip you're given its
title, hook explanation, and the actual transcript text spoken during its time range (plus
any human feedback already recorded about it, if available). Score it honestly — you are
not the one who picked it, so you have no reason to defend a bad choice.

Score each clip's reward_score from -10 to +10:
- Negative scores (-10 to -1): the clip is boring/flat with no real payoff, starts or ends
  mid-sentence, has no hook that would make a stranger keep watching, or is cut in a way
  that rips it out of context so a first-time viewer would be confused about what's
  happening.
- Positive scores (+1 to +10): the clip stands on its own, has a clear hook, and would
  plausibly perform well as a standalone short.

If reward_score is negative, you MUST identify exactly what went wrong and phrase it as a
short, general, reusable "AVOID" rule that would prevent this specific failure mode in
future clip selection (e.g. "Schneide niemals Clips, die mit einem unvollständigen Satz
enden."). Be concrete about the failure, not vague ("Clip war schlecht" is not acceptable).

If reward_score is 7 or higher, optionally phrase what made it work as a short, general,
reusable "DO" rule for future clip selection. Leave it out if there's nothing generalizable.

Write all rule text in German, matching the style of the existing guidelines you may be
shown as examples.
"""


class ClipVerdict(BaseModel):
    clip_title: str = Field(description="The title of the clip being scored, copied exactly")
    reward_score: int = Field(description="Reward score from -10 (bad) to +10 (great)", ge=-10, le=10)
    reasoning: str = Field(description="Brief explanation of the score")
    avoid_rule: Optional[str] = Field(
        default=None,
        description="Required if reward_score is negative: a general, reusable rule to avoid this mistake",
    )
    positive_rule: Optional[str] = Field(
        default=None,
        description="Optional if reward_score >= 7: a general, reusable rule describing what worked",
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


def build_critic_user_message(
    clips: List[dict], transcript: Optional[dict], feedback_by_title: Dict[str, List[str]]
) -> str:
    blocks = []
    for clip in clips:
        title = clip.get("title", "Untitled")
        feedback = feedback_by_title.get(title)
        block = (
            f"Titel: {title}\n"
            f"Hook-Begründung (vom Auswahl-Modell): {clip.get('hook_explanation', '')}\n"
            f"Transkript im Clip-Zeitbereich: {extract_clip_transcript_text(transcript, clip['start_time'], clip['end_time'])}"
        )
        if feedback:
            block += "\nMenschliches Feedback zu diesem Clip: " + " | ".join(feedback)
        blocks.append(block)

    return "Bewerte die folgenden Clips:\n\n" + "\n\n---\n\n".join(blocks)


def run_critic(
    clips: List[dict],
    transcript: Optional[dict],
    feedback_by_title: Dict[str, List[str]],
    model: str = MODEL,
) -> CriticBatch:
    client = OpenAI()
    user_message = build_critic_user_message(clips, transcript, feedback_by_title)

    logger.info("Sending %d clip(s) to the critic model (%s) for scoring", len(clips), model)
    try:
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format=CriticBatch,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )
    except Exception as e:
        logger.error("Critic LLM call failed: %s", e)
        raise

    choice = completion.choices[0]
    parsed = choice.message.parsed
    if parsed is None:
        logger.warning(
            "Critic response could not be parsed (finish_reason=%s) — no guidelines will be "
            "derived from this run.", choice.finish_reason,
        )
        return CriticBatch(verdicts=[])

    return parsed


def parse_guidelines_file(content: str) -> Tuple[List[str], List[str]]:
    """Public so app.py's "KI Gehirn" tab can reuse the exact same parsing logic instead of
    re-implementing it to render the guidelines file in the dashboard."""
    positive: List[str] = []
    negative: List[str] = []
    current: Optional[List[str]] = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[+]"):
            current = positive
            continue
        if stripped.startswith("[-]"):
            current = negative
            continue
        if stripped.startswith("- ") and current is not None:
            current.append(stripped[2:].strip())

    return positive, negative


def load_existing_guidelines() -> Tuple[List[str], List[str]]:
    if not analyze.AI_GUIDELINES_PATH.exists():
        return [], []
    try:
        content = analyze.AI_GUIDELINES_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Could not read existing %s, starting fresh: %s", analyze.AI_GUIDELINES_PATH, e)
        return [], []
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


def save_guidelines(positive: List[str], negative: List[str]) -> None:
    lines = ["[+] POSITIVE REWARDS (DO THIS):"]
    lines += [f"- {rule}" for rule in positive] if positive else ["- (noch keine)"]
    lines.append("")
    lines.append("[-] PENALTIES (NEVER DO THIS):")
    lines += [f"- {rule}" for rule in negative] if negative else ["- (noch keine)"]

    analyze.AI_GUIDELINES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        "Saved %d positive and %d negative guideline(s) to %s",
        len(positive), len(negative), analyze.AI_GUIDELINES_PATH,
    )


def run_training_loop(clips_path: Optional[Path] = None, model: str = MODEL) -> Tuple[Path, CriticBatch]:
    """Run the critic over a batch of clips, update ai_guidelines.txt, and return
    (guidelines_path, batch) — `batch` (the per-clip reward_score verdicts) is what lets a
    caller like auto_pilot.py decide which clips to purge, without re-running the critic."""
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

    batch = run_critic(clips, transcript, feedback_by_title, model)

    new_positive = []
    new_negative = []
    for verdict in batch.verdicts:
        logger.info(
            "Critic verdict: '%s' -> reward_score=%d (%s)",
            verdict.clip_title, verdict.reward_score, verdict.reasoning,
        )
        if verdict.reward_score < 0:
            if verdict.avoid_rule:
                new_negative.append(verdict.avoid_rule)
            else:
                logger.warning(
                    "Clip '%s' got a negative score (%d) but no avoid_rule was provided",
                    verdict.clip_title, verdict.reward_score,
                )
        elif verdict.reward_score >= POSITIVE_RULE_THRESHOLD and verdict.positive_rule:
            new_positive.append(verdict.positive_rule)

    existing_positive, existing_negative = load_existing_guidelines()
    save_guidelines(
        _dedupe(existing_positive + new_positive),
        _dedupe(existing_negative + new_negative),
    )

    return analyze.AI_GUIDELINES_PATH, batch


def main():
    parser = argparse.ArgumentParser(
        description="Run the critic training loop over the clips from a past analyze.py run."
    )
    parser.add_argument("--clips", type=Path, default=None, help="Path to a *_clips.json file (defaults to the newest one)")
    parser.add_argument("--model", default=MODEL, help="Critic model name")
    args = parser.parse_args()

    run_training_loop(args.clips, args.model)


if __name__ == "__main__":
    main()
