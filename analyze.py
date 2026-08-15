"""Analysis step: send the transcript to an LLM and select viral-worthy clips as structured JSON."""

import argparse
import json
import logging
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
TRANSCRIPTION_PATH = TEMP_DIR / "transcription.json"
OUTPUT_PATH = TEMP_DIR / "clips.json"
FEEDBACK_PATH = Path("feedback.json")
TOP_PERFORMERS_PATH = Path("top_performers.json")
MODEL = "gpt-4o-mini"

MIN_CLIP_DURATION = 10
MAX_CLIP_DURATION = 60
MIN_CLIPS_TARGET = 5
MAX_CLIPS_TARGET = 10

BASE_SYSTEM_PROMPT = f"""You are a highly-paid TikTok & YouTube Shorts strategist AND a master storyteller /
content director. Your goal is absolute virality, extremely high watch-time, and perfect hooks —
but NEVER at the cost of a clip that doesn't actually make sense on its own.

Analyze the ENTIRE provided timestamped transcript from start to finish and hunt for EVERY
potentially viral moment — do not stop after the first few good ones, keep scanning the whole
transcript.

Prioritize moments with:
- Humor (jokes, punchlines, funny reactions)
- Strong emotion (surprise, excitement, anger, vulnerability)
- Action or a clear narrative beat (a story with a setup and payoff)
- Controversial, surprising, or bold statements

Rules:
- Volume: Depending on the video length, return at least {MIN_CLIPS_TARGET} and up to
  {MAX_CLIPS_TARGET} clips. Scan the full transcript for distinct viral moments instead of
  settling for a handful — a long transcript with few clips returned is a failure.
- Length: Each clip MUST be at least {MIN_CLIP_DURATION} seconds and at most {MAX_CLIP_DURATION}
  seconds long. Clips shorter than {MIN_CLIP_DURATION} seconds are useless and must never be
  returned.
- Hooks: Every clip MUST begin exactly on its hook — a controversial, exciting, or funny
  statement that grabs attention in the first moment. Trim away dead air, silence, or filler
  before the hook; do not start a clip mid-thought or with a slow lead-in.
- Narrative coherence: Every clip MUST make real sense as a self-contained story. Only pick
  sequences with a logical beginning (a problem, question, or setup) and a clear end (the
  answer, punchline, or conclusion). A clip that raises a question but is cut before the answer,
  or that pays off a joke whose setup isn't included, is a failure — extend the start_time/
  end_time to cover the full beginning-to-end arc instead of truncating it.
- No mid-sentence cuts: A clip must never start or end in the middle of a sentence. The first
  sentence must be a complete, understandable opening, and the last sentence must be fully
  spoken to its end — never cut off a word, thought, or sentence early to hit a shorter duration.
- Payoff integrity: When you select a moment for its aha-moment, joke, hard fact, or emotional
  reaction, always include its full setup and its full payoff in the same clip — never split a
  setup from its punchline/answer across the clip boundary.
- start_time and end_time must be timestamps that actually occur in the transcript (in seconds).
- Only select clips that work as a standalone moment without extra context.
- Do not invent content that is not present in the transcript.
"""


class Clip(BaseModel):
    start_time: float = Field(description="Clip start time in seconds")
    end_time: float = Field(description="Clip end time in seconds")
    title: str = Field(description="Short, catchy title for the clip")
    hook_explanation: str = Field(description="Why this moment is a good/viral hook")
    viral_score: int = Field(description="Viral potential score from 1 (low) to 10 (high)", ge=1, le=10)


class ClipSelection(BaseModel):
    clips: List[Clip]


def format_timestamp(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes):02d}:{secs:04.1f}"


def load_feedback_section() -> str:
    if not FEEDBACK_PATH.exists():
        return ""

    try:
        with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
            feedback_entries = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s, ignoring past feedback: %s", FEEDBACK_PATH, e)
        return ""

    if not feedback_entries:
        return ""

    lines = [
        f"- (Clip \"{entry.get('clip_title', 'unbekannt')}\"): {entry.get('feedback', '')}"
        for entry in feedback_entries
    ]
    logger.info("Loaded %d past feedback entries from %s", len(lines), FEEDBACK_PATH)
    return (
        "\n\nWICHTIGE REGELN AUS VERGANGENEM FEEDBACK "
        "(du MUSST diese bei der Auswahl neuer Szenen zwingend berücksichtigen):\n"
        + "\n".join(lines)
    )


def load_top_performers_section() -> str:
    """Optional few-shot examples: title/hook pairs from clips that performed well on this
    channel, so the LLM learns the house style instead of guessing at "viral" in the abstract."""
    if not TOP_PERFORMERS_PATH.exists():
        return ""

    try:
        with open(TOP_PERFORMERS_PATH, "r", encoding="utf-8") as f:
            examples = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s, skipping few-shot examples: %s", TOP_PERFORMERS_PATH, e)
        return ""

    if not examples:
        return ""

    lines = [f"- \"{ex.get('title', 'Untitled')}\": {ex.get('hook', '')}" for ex in examples]
    logger.info("Loaded %d top-performer example(s) from %s", len(lines), TOP_PERFORMERS_PATH)
    return (
        "\n\nTOP-PERFORMING REFERENCE CLIPS ON THIS CHANNEL "
        "(study why these hooks/titles worked and apply the same pattern when judging new clips):\n"
        + "\n".join(lines)
    )


def build_system_prompt() -> str:
    return BASE_SYSTEM_PROMPT + load_top_performers_section() + load_feedback_section()


def save_feedback(clip_title: str, feedback_text: str) -> None:
    """Append a piece of user feedback about a rendered clip to feedback.json."""
    entries = []
    if FEEDBACK_PATH.exists():
        try:
            with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not read %s, starting a fresh feedback list: %s", FEEDBACK_PATH, e)
            entries = []

    entries.append({"clip_title": clip_title, "feedback": feedback_text})

    with open(FEEDBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    logger.info("Saved feedback for clip '%s'", clip_title)


def load_transcript(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_prompt_text(transcript: dict) -> str:
    segments = transcript.get("segments", [])
    if not segments:
        raise ValueError("Transcript contains no segments")

    lines = [f"[{format_timestamp(seg['start'])}] {seg['text']}" for seg in segments]
    return "\n".join(lines)


def select_clips(transcript_text: str, model: str = MODEL) -> ClipSelection:
    client = OpenAI()
    system_prompt = build_system_prompt()

    logger.info("Sending transcript to %s for scene selection", model)
    try:
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Transcript:\n{transcript_text}"},
            ],
            response_format=ClipSelection,
        )
    except Exception as e:
        logger.error("LLM API call failed: %s", e)
        raise

    parsed = completion.choices[0].message.parsed
    if parsed is None or not parsed.clips:
        raise RuntimeError("LLM returned no valid clips")

    valid_clips = [
        clip for clip in parsed.clips
        if MIN_CLIP_DURATION <= (clip.end_time - clip.start_time) <= MAX_CLIP_DURATION
    ]
    dropped = len(parsed.clips) - len(valid_clips)
    if dropped:
        logger.warning("Dropped %d clip(s) outside the %d-%ds duration bounds", dropped, MIN_CLIP_DURATION, MAX_CLIP_DURATION)

    # No raise here on empty: the caller (analyze()) has the raw transcript and can fall
    # back to the longest available segment instead of failing the whole pipeline.
    return ClipSelection(clips=valid_clips)


def find_longest_segment_fallback(transcript: dict) -> "ClipSelection":
    """Pick the longest contiguous run of transcript segments (10-60s) as a single clip.

    Used when the LLM returns zero clips within the duration bounds — e.g. a short test
    chunk with no single moment the model considered strong enough — so the pipeline can
    still produce something instead of failing outright.
    """
    segments = transcript.get("segments", [])
    if not segments:
        raise RuntimeError("Cannot build a fallback clip: transcript has no segments")

    # Segments separated by more than this many seconds of silence are not "contiguous" —
    # merging across a real gap would splice unrelated, disconnected moments into one clip.
    max_gap_seconds = 1.5

    best = None  # (start_idx, end_idx, duration)
    for i in range(len(segments)):
        start = segments[i]["start"]
        for j in range(i, len(segments)):
            if j > i and (segments[j]["start"] - segments[j - 1]["end"]) > max_gap_seconds:
                break  # gap breaks contiguity; extending further would no longer be one span
            duration = segments[j]["end"] - start
            if duration > MAX_CLIP_DURATION:
                break
            if duration >= MIN_CLIP_DURATION and (best is None or duration > best[2]):
                best = (i, j, duration)

    if best is None:
        raise RuntimeError(
            f"No contiguous transcript span of at least {MIN_CLIP_DURATION}s was found for a fallback clip"
        )

    i, j, duration = best
    logger.warning(
        "LLM returned no clips within bounds; falling back to the longest available segment (%.1fs)", duration
    )
    fallback_clip = Clip(
        start_time=segments[i]["start"],
        end_time=segments[j]["end"],
        title="Highlight-Moment",
        hook_explanation="Automatisch ausgewählt: längstes verfügbares zusammenhängendes Segment (Fallback, da die KI keine passenden Clips fand).",
        viral_score=5,
    )
    return ClipSelection(clips=[fallback_clip])


def analyze(transcription_path: Path = TRANSCRIPTION_PATH, model: str = MODEL) -> Path:
    load_dotenv()

    transcript = load_transcript(transcription_path)
    transcript_text = build_prompt_text(transcript)

    selection = select_clips(transcript_text, model)
    if not selection.clips:
        selection = find_longest_segment_fallback(transcript)

    TEMP_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(selection.model_dump(), f, ensure_ascii=False, indent=2)

    logger.info("Analysis complete: %s (%d clips)", OUTPUT_PATH, len(selection.clips))
    return OUTPUT_PATH


def main():
    parser = argparse.ArgumentParser(description="Select viral clips from a transcript via an LLM.")
    parser.add_argument("transcript", type=Path, nargs="?", default=TRANSCRIPTION_PATH, help="Path to transcription.json")
    parser.add_argument("--model", default=MODEL, help="LLM model name")
    args = parser.parse_args()

    analyze(args.transcript, args.model)


if __name__ == "__main__":
    main()
