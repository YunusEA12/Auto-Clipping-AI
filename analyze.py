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
MODEL = "gpt-4o-2024-08-06"

SYSTEM_PROMPT = """You are an expert short-form video editor who finds viral moments in raw transcripts.
Analyze the provided timestamped transcript and select the strongest standalone clips.

Prioritize moments with:
- Humor (jokes, punchlines, funny reactions)
- Strong emotion (surprise, excitement, anger, vulnerability)
- Action or a clear narrative beat (a story with a setup and payoff)
- A hook in the first few seconds that makes someone stop scrolling

Rules:
- Each clip must be between 30 and 60 seconds long.
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

    logger.info("Sending transcript to %s for scene selection", model)
    try:
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
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

    return parsed


def analyze(transcription_path: Path = TRANSCRIPTION_PATH, model: str = MODEL) -> Path:
    load_dotenv()

    transcript = load_transcript(transcription_path)
    transcript_text = build_prompt_text(transcript)

    selection = select_clips(transcript_text, model)

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
