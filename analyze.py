"""Analysis step: send the transcript to an LLM and select viral-worthy clips as structured JSON."""

import argparse
import json
import logging
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
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
AI_GUIDELINES_PATH = Path("ai_guidelines.txt")
MODEL = "gpt-4o-mini"

MIN_CLIP_DURATION = 8
MAX_CLIP_DURATION = 120
MIN_CLIPS_TARGET = 15
MAX_CLIPS_TARGET = 20

# Generous headroom for ~20 detailed clips (title + hook_explanation + scores) as structured
# JSON, well within gpt-4o-mini's 16384-token output cap — without this, an unbounded
# response risks getting cut off mid-JSON exactly when MAX_CLIPS_TARGET is raised.
MAX_COMPLETION_TOKENS = 8192

ENERGY_WINDOW_SECONDS = 2.0
ENERGY_SPIKE_THRESHOLD_STD = 1.5

# High-engagement keywords (German + English, streaming/gaming-flavored) — clips containing
# these words tend to mark genuine reactions rather than neutral narration.
TRIGGER_WORDS = [
    "krass", "wahnsinn", "digga", "alter", "geil", "hammer", "unglaublich",
    "ehrlich", "kein bock", "warte", "bro", "was zur",
    "insane", "crazy", "no way", "let's go", "oh my god", "clutch", "wild",
]

BASE_SYSTEM_PROMPT = f"""You are a highly-paid TikTok & YouTube Shorts strategist AND a master storyteller /
content director. Your job is to find every clip in this transcript that has genuinely high
viral potential — a strong hook, a clear self-contained narrative, and real energy. Quality is
the bar you select against, not a target clip count.

Analyze the ENTIRE provided timestamped transcript from start to finish and hunt for EVERY
usable moment — do not stop after the first few good ones, keep scanning the whole transcript.
ONLY extract a clip if it clears the bar above. If the segment is boring, lacks the context
needed to stand alone, or violates any of the "PENALTIES" rules you may be shown further below,
leave it out entirely — do not force a clip out of mediocre content just to have something to
show.

Look for (any of these is enough — a clip does NOT need to hit several at once):
- Humor (jokes, punchlines, funny reactions)
- Emotion, even mild (surprise, excitement, mild annoyance, curiosity)
- Action or a clear narrative beat (a story with a setup and payoff)
- Controversial, surprising, or bold statements
- Informative or interesting moments that are simply worth watching, even without a big reaction

Rules:
- SINNHAFTIGKEIT (top priority — applies before every other rule below): Ein Clip MUSS
  logisch zusammenhängend sein. Er muss einen klaren Anfang, einen nachvollziehbaren
  Kontext und ein abgeschlossenes Ende haben. Halbe Sätze oder zusammenhangsloses Gefasel
  (ohne Pointe) MÜSSEN ignoriert werden. Wenn ein Segment inhaltlich keinen Sinn ergibt,
  gib lieber eine leere Liste [] zurück, anstatt einen unlogischen Clip zu erzwingen.
- Quality gate, not a quota: Only extract a clip if it clearly has high viral potential (strong
  hook, clear narrative arc, real energy or genuine interest). If the whole transcript is boring,
  none of it has the context needed for a standalone clip, or every candidate violates a
  PENALTIES rule, you MUST return an empty clips list. An empty list is a valid, correct, and
  preferred result over forcing a mediocre clip out of weak material — never pad the output to
  hit a target count.
- Volume: When the material genuinely supports it, aim for {MIN_CLIPS_TARGET}-{MAX_CLIPS_TARGET}
  clips by scanning the full transcript for distinct usable moments — but this target never
  overrides the quality gate above. Fewer clips, or zero, is correct when that's what the
  material actually supports.
- Length: Each clip MUST be at least {MIN_CLIP_DURATION} seconds and at most {MAX_CLIP_DURATION}
  seconds long. When in doubt, make a clip LONGER rather than shorter — a slightly-too-long clip
  is fine, but a clip that starts mid-sentence because it was cut too tight is not.
- Setup context: Before the actual punchline/payoff/energy spike, include roughly 10-15 seconds
  of setup so a viewer who has no other context understands what's going on. Avoid bare 10-second
  clips that start mid-sentence right on top of the punchline with no lead-in — the viewer needs
  time to orient before the payoff lands.
- Hooks: Every clip should begin on or shortly before its hook — something that gives the viewer
  a reason to keep watching. Trim away long dead air or filler before it, but don't trim so tight
  that the setup context above gets lost.
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
- Energy tolerance: The user message may include a list of AUDIO ENERGY SPIKES — timestamps
  where the raw audio loudness suddenly surged (shouting, laughing, a sudden reaction). A high
  energy spike lining up with a strong hook is a great bonus signal, but it is NOT required —
  calmer, informative, or mildly funny moments are completely fine too (an energy_rating around
  5/10 is a perfectly normal, acceptable clip, not a failure). Do not discard a narratively solid
  clip just because it isn't a maximum-intensity outburst.
- Trigger words: These words mark genuine high-engagement reactions rather than neutral
  narration — prefer clips that contain one or more of them when otherwise similar, but their
  absence is not a reason to reject an otherwise good clip:
  {', '.join(TRIGGER_WORDS)}
- Description/caption: Every clip also needs a ready-to-post TikTok/Shorts caption — a short,
  punchy hook written for social media (not a plain summary of the clip), copy-paste ready for
  the creator to post as-is.
- Hashtags: Every clip needs 5 to 7 viral, content-relevant hashtags, each including the
  leading '#' (e.g. "#gaming", "#twitchclips", "#viral", "#fy"). Mix broad reach tags (#fyp,
  #viral, #shorts) with a couple specific to the actual content of the clip.
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
    energy_rating: int = Field(
        description="Emotional/audio energy rating from 1 (calm) to 10 (explosive) — how much "
        "the audio energy spikes and the content hook reinforce each other in this clip",
        ge=1, le=10,
    )
    description: str = Field(
        description="A ready-to-post TikTok/Shorts caption with a strong hook — written for "
        "social media, copy-paste ready, not just a plain summary of the clip"
    )
    hashtags: List[str] = Field(
        description="5 to 7 viral, content-relevant hashtags, each including the leading '#' "
        "(e.g. '#gaming', '#twitchclips', '#viral', '#fy')"
    )


class ClipSelection(BaseModel):
    clips: List[Clip]


class LLMResponseIncomplete(Exception):
    """Raised when the selector LLM's response was technically unusable (truncated mid-JSON
    or unparseable) — distinct from the model validly returning ClipSelection(clips=[]),
    which is a legitimate "nothing here clears the quality bar" decision, not a failure."""


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


def build_profile_section(profile: Optional[dict]) -> str:
    """Streamer-specific context injected on top of the general rules — the "Streamer-
    Mitarbeiter" multi-agent piece: each creator gets their own house style without
    touching the base prompt. Accepts a plain dict (profiles.StreamerProfile.model_dump())
    so this module doesn't need to depend on the profiles module."""
    if not profile:
        return ""

    parts = []
    context_prompt = (profile.get("context_prompt") or "").strip()
    if context_prompt:
        parts.append(f"STREAMER-SPECIFIC CONTEXT for {profile.get('name', 'this streamer')}:\n{context_prompt}")

    trigger_words = profile.get("trigger_words") or []
    if trigger_words:
        parts.append(
            f"STREAMER-SPECIFIC TRIGGER WORDS for {profile.get('name', 'this streamer')} "
            "(in addition to the general trigger words above — prioritize clips containing "
            f"these): {', '.join(trigger_words)}"
        )

    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


def load_ai_guidelines_section() -> str:
    """Learned guidelines from train_loop.py's critic pass over past clips — a running,
    self-updating rulebook of what to repeat and what to never do again."""
    if not AI_GUIDELINES_PATH.exists():
        return ""

    try:
        content = AI_GUIDELINES_PATH.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("Could not read %s, skipping learned guidelines: %s", AI_GUIDELINES_PATH, e)
        return ""

    if not content:
        return ""

    logger.info("Loaded learned guidelines from %s", AI_GUIDELINES_PATH)
    return (
        "\n\nLEARNED GUIDELINES FROM PAST TRAINING FEEDBACK (a critic model reviewed "
        "previously selected clips and derived these — treat them as hard constraints, not "
        "soft suggestions):\n" + content +
        "\n\nIf a candidate segment matches ANY characteristic listed under "
        '"[-] PENALTIES (NEVER DO THIS)" above, discard it immediately and do not include it '
        "at all — do not merely down-rank it. Segments matching a "
        '"[+] POSITIVE REWARDS (DO THIS)" pattern should be prioritized over otherwise '
        "similar candidates."
    )


def build_system_prompt(profile: Optional[dict] = None) -> str:
    return (
        BASE_SYSTEM_PROMPT
        + load_top_performers_section()
        + load_feedback_section()
        + build_profile_section(profile)
        + load_ai_guidelines_section()
    )


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


def clips_path_for(transcription_path: Path) -> Path:
    """Per-video clips path (e.g. temp/my_video_clips.json), derived from the matching
    *_transcription.json path so each source video gets its own clips file instead of
    every run clobbering a shared temp/clips.json."""
    stem = transcription_path.stem
    if stem.endswith("_transcription"):
        stem = stem[: -len("_transcription")]
    return TEMP_DIR / f"{stem}_clips.json"


def transcription_path_for(clips_path: Path) -> Path:
    """Inverse of clips_path_for(): the *_transcription.json matching a given *_clips.json,
    used by train_loop.py to pull back the transcript context for clips it's evaluating."""
    stem = clips_path.stem
    if stem.endswith("_clips"):
        stem = stem[: -len("_clips")]
    return TEMP_DIR / f"{stem}_transcription.json"


def load_transcript(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def calculate_energy_score(audio_segment: np.ndarray) -> float:
    """Loudness VARIANCE within a chunk of PCM samples, not just raw volume.

    Splits the segment into ~20 sub-frames, computes RMS loudness per sub-frame, and
    returns the standard deviation across those sub-frames. A shout or burst of laughter
    swings loudness up and down hard (high variance); steady narration stays flat (low
    variance) even if it's not quiet — variance is a better "something just happened"
    signal than raw volume alone.
    """
    if audio_segment.size == 0:
        return 0.0

    frame_size = max(1, len(audio_segment) // 20)
    frame_rms = [
        np.sqrt(np.mean(np.square(audio_segment[i:i + frame_size])))
        for i in range(0, len(audio_segment), frame_size)
        if len(audio_segment[i:i + frame_size]) > 0
    ]
    return float(np.std(frame_rms)) if frame_rms else 0.0


def _load_pcm_samples(wav_path: Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    return samples, sample_rate


def scan_energy_windows(wav_path: Path, window_seconds: float = ENERGY_WINDOW_SECONDS) -> List[Tuple[float, float]]:
    """Energy score for every fixed-size window across the whole track: [(timestamp, score), ...]."""
    if not wav_path.exists():
        logger.warning("Audio file not found for energy analysis: %s", wav_path)
        return []

    try:
        samples, sample_rate = _load_pcm_samples(wav_path)
    except (wave.Error, OSError) as e:
        logger.warning("Could not read audio for energy analysis, skipping: %s", e)
        return []

    window_size = int(window_seconds * sample_rate)
    if window_size <= 0 or samples.size == 0:
        return []

    return [
        (start / sample_rate, calculate_energy_score(samples[start:start + window_size]))
        for start in range(0, len(samples), window_size)
    ]


def find_energy_spikes(window_scores: List[Tuple[float, float]]) -> List[dict]:
    """Statistical outlier windows — the audio equivalent of "a moment just happened"."""
    if not window_scores:
        return []

    values = np.array([score for _, score in window_scores])
    mean, std = float(values.mean()), float(values.std())
    if std == 0:
        return []

    threshold = mean + ENERGY_SPIKE_THRESHOLD_STD * std
    spikes = [
        {"timestamp": round(ts, 1), "energy_score": round(score, 1)}
        for ts, score in window_scores
        if score > threshold
    ]
    logger.info("Detected %d energy spike(s) (mean=%.1f, std=%.1f)", len(spikes), mean, std)
    return spikes


def build_energy_prompt_section(spikes: List[dict]) -> str:
    if not spikes:
        return ""
    lines = [f"[{format_timestamp(s['timestamp'])}] ENERGY SPIKE (intensity={s['energy_score']:.0f})" for s in spikes]
    return (
        "\n\nAUDIO ENERGY SPIKES (moments where the raw audio loudness suddenly surged — "
        "likely shouting, laughing, or a sudden reaction; cross-reference these timestamps "
        "with the transcript above):\n" + "\n".join(lines)
    )


def rate_clip_energy(clip: Clip, window_scores: List[Tuple[float, float]]) -> int:
    """Real 1-10 energy rating for a clip's time range, measured from the actual audio
    rather than trusting the LLM's own guess — the peak windowed score inside the clip,
    normalized against the full track's observed range."""
    if not window_scores:
        return clip.energy_rating

    all_scores = [score for _, score in window_scores]
    clip_scores = [score for ts, score in window_scores if clip.start_time <= ts < clip.end_time]

    lo, hi = min(all_scores), max(all_scores)
    if not clip_scores or hi == lo:
        return clip.energy_rating

    normalized = (max(clip_scores) - lo) / (hi - lo)
    return max(1, min(10, round(1 + normalized * 9)))


def build_prompt_text(transcript: dict) -> str:
    segments = transcript.get("segments", [])
    if not segments:
        raise ValueError("Transcript contains no segments")

    lines = [f"[{format_timestamp(seg['start'])}] {seg['text']}" for seg in segments]
    return "\n".join(lines)


def select_clips(
    transcript_text: str,
    energy_spikes: Optional[List[dict]] = None,
    window_scores: Optional[List[Tuple[float, float]]] = None,
    model: str = MODEL,
    profile: Optional[dict] = None,
) -> ClipSelection:
    client = OpenAI()
    system_prompt = build_system_prompt(profile)
    energy_section = build_energy_prompt_section(energy_spikes or [])

    logger.info("Sending transcript to %s for scene selection", model)
    try:
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Transcript:\n{transcript_text}{energy_section}"},
            ],
            response_format=ClipSelection,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )
    except Exception as e:
        logger.error("LLM API call failed: %s", e)
        raise

    choice = completion.choices[0]
    parsed = choice.message.parsed

    # A technically-unusable response (truncated mid-JSON, or unparseable) is a genuine
    # failure and raises LLMResponseIncomplete so analyze() knows to fall back to
    # find_longest_segment_fallback(). This is distinct from the model validly parsing but
    # deliberately choosing zero clips (handled below) — that's an intentional quality-gate
    # decision, not a failure, and must NOT trigger the fallback.
    if choice.finish_reason == "length":
        # Checked before the generic parsed-is-None case below: a truncated response almost
        # always fails to parse too, so this more specific/actionable message would otherwise
        # never be reached.
        raise LLMResponseIncomplete(
            f"LLM response for {model} was truncated (finish_reason=length, "
            f"max_completion_tokens={MAX_COMPLETION_TOKENS}) — the JSON likely broke mid-clip"
        )
    if parsed is None:
        raise LLMResponseIncomplete(
            f"LLM response for {model} could not be parsed into ClipSelection "
            f"(finish_reason={choice.finish_reason}, refusal={getattr(choice.message, 'refusal', None)!r})"
        )

    if not parsed.clips:
        # The model validly ran and deliberately decided nothing in this transcript clears
        # the quality bar (see BASE_SYSTEM_PROMPT's "Quality gate, not a quota" rule) — a
        # correct, intentional result, not a failure. No fallback clip should be generated.
        logger.info("Kein spannender Content gefunden — die KI hat bewusst keine Clips ausgewählt.")
        return ClipSelection(clips=[])

    valid_clips = [
        clip for clip in parsed.clips
        if MIN_CLIP_DURATION <= (clip.end_time - clip.start_time) <= MAX_CLIP_DURATION
    ]
    dropped = len(parsed.clips) - len(valid_clips)
    if dropped:
        logger.warning("Dropped %d clip(s) outside the %d-%ds duration bounds", dropped, MIN_CLIP_DURATION, MAX_CLIP_DURATION)

    if window_scores:
        valid_clips = [
            clip.model_copy(update={"energy_rating": rate_clip_energy(clip, window_scores)})
            for clip in valid_clips
        ]

    # No raise here on empty: the caller (analyze()) has the raw transcript and can fall
    # back to the longest available segment instead of failing the whole pipeline.
    return ClipSelection(clips=valid_clips)


def find_longest_segment_fallback(transcript: dict) -> "ClipSelection":
    """Pick the longest contiguous run of transcript segments (10-60s) as a single clip.

    Used ONLY when select_clips() couldn't get a usable response at all (LLMResponseIncomplete
    — a truncated or unparseable API response), so the pipeline still produces something
    instead of failing outright. NOT used when the model validly decided there's nothing
    worth clipping — that's a correct result, not a failure, and is left as an empty list.
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
        "LLM response was unusable; falling back to the longest available segment (%.1fs)", duration
    )
    fallback_clip = Clip(
        start_time=segments[i]["start"],
        end_time=segments[j]["end"],
        title="Highlight-Moment",
        hook_explanation="Automatisch ausgewählt: längstes verfügbares zusammenhängendes Segment (Fallback, da die KI keine passenden Clips fand).",
        viral_score=5,
        energy_rating=5,
        description="Das solltest du dir ansehen 👀",
        hashtags=["#gaming", "#twitchclips", "#viral", "#fy", "#stream"],
    )
    return ClipSelection(clips=[fallback_clip])


def find_audio_path() -> Optional[Path]:
    """Best-effort discovery of the extracted .wav when the caller doesn't pass one explicitly."""
    candidates = list(TEMP_DIR.glob("*.wav"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        logger.warning("Multiple .wav files found in %s, skipping energy analysis: %s", TEMP_DIR, candidates)
    return None


def analyze(
    transcription_path: Path = TRANSCRIPTION_PATH,
    model: str = MODEL,
    audio_path: Optional[Path] = None,
    profile: Optional[dict] = None,
) -> Path:
    load_dotenv()

    transcript = load_transcript(transcription_path)
    transcript_text = build_prompt_text(transcript)

    if audio_path is None:
        audio_path = find_audio_path()

    window_scores: List[Tuple[float, float]] = []
    energy_spikes: List[dict] = []
    if audio_path is not None:
        window_scores = scan_energy_windows(audio_path)
        energy_spikes = find_energy_spikes(window_scores)
    else:
        logger.info("No audio file available; skipping emotional-energy scoring")

    try:
        selection = select_clips(transcript_text, energy_spikes, window_scores, model, profile)
    except LLMResponseIncomplete as e:
        logger.warning("%s; falling back to the longest available segment instead of failing.", e)
        selection = find_longest_segment_fallback(transcript)

    if not selection.clips:
        logger.info(
            "Kein spannender Content gefunden — die KI hat für dieses Segment absichtlich "
            "keine Clips ausgewählt. Kein Fallback-Clip wird erzeugt."
        )

    output_path = clips_path_for(transcription_path)
    TEMP_DIR.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(selection.model_dump(), f, ensure_ascii=False, indent=2)

    logger.info("Analysis complete: %s (%d clips)", output_path, len(selection.clips))
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Select viral clips from a transcript via an LLM.")
    parser.add_argument("transcript", type=Path, nargs="?", default=TRANSCRIPTION_PATH, help="Path to transcription.json")
    parser.add_argument("--model", default=MODEL, help="LLM model name")
    parser.add_argument("--audio", type=Path, default=None, help="Path to the extracted .wav (auto-detected if omitted)")
    parser.add_argument("--profile", default=None, help="Streamer profile name (profiles/<name>.json)")
    args = parser.parse_args()

    profile_dict = None
    if args.profile:
        import profiles as profiles_module
        profile_dict = profiles_module.load_profile(args.profile).model_dump()

    analyze(args.transcript, args.model, args.audio, profile_dict)


if __name__ == "__main__":
    main()
