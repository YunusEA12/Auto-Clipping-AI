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
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from fractions import Fraction

import analyze
import atomic_io
import llm_utils
import process as process_module

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

TEMP_DIR = Path("temp")
VIRAL_MEMORY_PATH = Path("viral_memory.json")
# Independently defined, matching auto_pilot.UPLOADED_CLIPS_DIR by convention rather than by
# import: auto_pilot.py already imports this module, so importing it back here would create
# a circular import.
UPLOADED_CLIPS_DIR = Path("uploaded_clips")

# 2026-08-19: migrated from OpenAI to Gemini — gemini-3.5-flash-lite is multimodal (confirmed
# live), so the same model now handles both the text-only fallback pass and the vision
# (preview-frame) pass; MODEL/VISION_MODEL stay as two separate constants (rather than
# collapsing to one) because the code path that picks between them is really about whether
# preview frames extracted successfully, not about which model to call — see run_critic()'s
# try/except structure. See analyze.MODEL's own comment for why this specific model (several
# originally-tried ones, including gemini-1.5-flash and gemini-2.5-flash/-lite, are retired).
MODEL = "gemini-3.5-flash-lite"
VISION_MODEL = "gemini-3.5-flash-lite"
MAX_COMPLETION_TOKENS = 4096

# reward_score >= this threshold is eligible to contribute a "DO" rule (not every positive
# clip is exceptional enough to generalize into a standing rule).
POSITIVE_RULE_THRESHOLD = 7

# A clip checked before this many hours have passed since upload has 0 (or near-0) views
# because TikTok hasn't finished distributing it yet, not because it flopped — feeding that
# to the critic as "viral performance data" would teach it "everything flops" from noise
# instead of a real signal. Found live 2026-08-18: viral_memory.json was full of 0-view
# entries checked minutes after upload, which load_viral_memory_section() would otherwise
# have handed to the critic unfiltered.
VIRAL_SIGNAL_MIN_AGE_HOURS = 24

_FRACTION_NAMES = {
    Fraction(1, 2): "half", Fraction(1, 3): "third", Fraction(2, 3): "two-thirds",
    Fraction(1, 4): "quarter", Fraction(3, 4): "three-quarters",
}


def _describe_fraction(fraction: Fraction) -> str:
    return _FRACTION_NAMES.get(fraction, f"{float(fraction) * 100:.0f}%")


def _describe_split_ratio() -> Tuple[str, str]:
    """Describes process_module.SPLIT_SCREEN_FACE_RATIO in prose, generated from the actual
    constant instead of restating it by hand — previously "top third / bottom two-thirds"
    was hardcoded English prose that happened to match SPLIT_SCREEN_FACE_RATIO = 1/3 by
    convention only, with nothing tying the two together if the ratio was ever re-tuned."""
    face_fraction = Fraction(process_module.SPLIT_SCREEN_FACE_RATIO).limit_denominator(12)
    return _describe_fraction(face_fraction), _describe_fraction(1 - face_fraction)


_FACE_ZONE_DESC, _GAME_ZONE_DESC = _describe_split_ratio()

CRITIC_SYSTEM_PROMPT = f"""You are a ruthless TikTok/Shorts content critic reviewing clips
that an upstream AI already selected from a longer video. For each clip you're given its
title, hook explanation, the actual transcript text spoken during its time range, any human
feedback already recorded about it, and — when available — 3 preview screenshots (near-
start, middle, near-end) plus real TikTok performance data for previously uploaded clips.
Score honestly — you are not the one who picked it, so you have no reason to defend a bad
choice.

When a clip's block includes a "Kontext DAVOR" section, that text is background ONLY — the
few seconds spoken immediately before the clip's own start_time — not part of the clip and
never itself judged. Use it purely to understand what a short clip is reacting to before you
decide whether the clip itself lacks context; a clip that makes complete sense once you read
that lead-in is NOT "confusing" or "missing context," even though its own isolated transcript
slice would look that way in isolation.

Evaluate TWO independent dimensions:

1. NARRATIVE: Judged with the "Kontext DAVOR" lead-in in mind (see above), is the clip
   coherent and engaging? Does it start or end mid-sentence in a way that actually breaks
   comprehension? Is there a real payoff, or is it boring/rambling with nothing happening?
   Explicitly do NOT penalize a clip just for being SHORT or for skipping a long setup: a
   quick reaction shot, a one-liner, a punchline, or a fast-paced Twitch-humor beat (laugh,
   callback, chat reaction, sudden reveal) is a completely normal, good clip on its own —
   score it on whether the moment itself lands, not on whether it has a full three-act
   structure. Only mark it down for narrative if, even with the lead-in context, it's
   genuinely unclear what's happening or the cut visibly cuts off the payoff itself
   (not the setup).

2. VISUAL COMPOSITION (only when screenshots are provided): This pipeline renders in
   whichever of three layouts actually fits the source footage — judge correctness against
   WHICHEVER ONE you're actually looking at, not a single fixed template:
   - Split-screen: a small facecam box, centered in the top {_FACE_ZONE_DESC}, over a
     separate, clearly visible gameplay area filling the bottom {_GAME_ZONE_DESC}. Correct
     when there genuinely IS a separate gameplay feed to show.
   - Full-cam: the speaker's face/upper body fills most or all of the frame, with NO separate
     gameplay area at all. This is CORRECT — not a mistake — whenever the whole source shot
     already IS the person (a wide or close "Just Chatting"-style shot). Do not penalize the
     absence of visible gameplay here; that would be judging it against the wrong template.
   - Blurred background: the speaker's face over a blurred/stretched version of the same
     source footage filling the rest of the frame (used when zero or multiple faces were
     detected). Correct as long as the blur itself looks clean, not glitchy or pixelated.
   Whichever layout it is, always flag genuine visual problems: glitches, duplicated footage,
   unexpected black bars, a misplaced or badly cropped face, or a broken/pixelated blur.
   Judge purely from what's visible in the screenshots, not from the transcript.

Score each clip's overall reward_score from -10 to +10, weighing both dimensions:
- Negative scores (-10 to -1): bad on either dimension — boring/flat with genuinely no payoff
  even accounting for the lead-in context, the cut visibly severs the payoff itself (not just
  a missing setup), OR broken visual composition (wrong facecam position, black bars,
  glitches, duplicated content). Being short or being a quick reaction/punchline is NOT by
  itself a reason to score negative.
- Positive scores (+1 to +10): the moment itself lands — a real hook, laugh, or payoff,
  understandable with its lead-in context — AND (when screenshots are available) correct,
  clean visual composition.

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
- If you're instead shown ACCEPTED CLIPS DATA (no real TikTok metrics yet, but clips that
  already scored highly and got published) and a recurring topic/energy/style pattern is
  visible across several of them, you MAY still set a viral_pattern_rule — phrase it a little
  more tentatively than one backed by real metrics, since it reflects the critic's own past
  judgment, not confirmed audience response. Never set viral_pattern_rule from BOTH sources
  at once; only one of the two sections is ever shown per run.

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


def _old_enough_for_viral_signal(entry: dict, now: datetime, min_age_hours: int = VIRAL_SIGNAL_MIN_AGE_HOURS) -> bool:
    """Whether enough real time has passed since upload for this entry's view/like count to
    mean anything. `uploaded_at` missing or unparseable is treated as NOT old enough —
    never treat ambiguous timing as a green light to feed possibly-fresh data to the critic,
    same never-guess-on-ambiguity principle as metrics_tracker.prune_viral_memory."""
    uploaded_at = entry.get("uploaded_at")
    if not uploaded_at:
        return False
    try:
        uploaded = datetime.fromisoformat(uploaded_at)
        return now - uploaded >= timedelta(hours=min_age_hours)
    except (TypeError, ValueError):
        # TypeError covers two cases ValueError doesn't: a non-string uploaded_at, and a
        # naive (no-timezone) uploaded_at subtracted against an aware `now` — viral_memory.json
        # is a hand-editable accumulator with no schema enforcement, so either is possible
        # even though the only current writer always writes tz-aware ISO strings.
        return False


def _entry_has_metrics(e: dict) -> bool:
    """True once at least one platform has a real count. Falls back to the legacy flat
    views/likes keys (see _format_platform_metrics()) for entries written before
    metrics_tracker.py split per-platform (2026-08-21) — some of those are frozen in that
    shape forever (their uploaded_clips/ sidecar was already deleted after two TikTok
    confirmations, so they can never be re-matched and rewritten in the new shape)."""
    return any(e.get(k) is not None for k in ("tiktok_views", "youtube_views", "views"))


def _format_platform_metrics(e: dict) -> str:
    """One clause per platform that actually has data. tiktok_views/likes fall back to the
    legacy flat views/likes keys — see _entry_has_metrics() for why those can't just be
    migrated away."""
    parts = []
    tiktok_views = e.get("tiktok_views", e.get("views"))
    if tiktok_views is not None:
        parts.append(f"TikTok {tiktok_views} views/{e.get('tiktok_likes', e.get('likes')) or 0} likes")
    youtube_views = e.get("youtube_views")
    if youtube_views is not None:
        parts.append(f"YouTube {youtube_views} views/{e.get('youtube_likes') or 0} likes")
    return ", ".join(parts)


def load_viral_memory_section() -> str:
    """Real per-platform view/like data for previously uploaded clips (written by
    metrics_tracker.py — TikTok via Studio scrape, YouTube via the Data API since 2026-08-21).
    Only clips that have actually been checked on at least one platform AND have had at least
    VIRAL_SIGNAL_MIN_AGE_HOURS since upload are included — a clip checked minutes after going
    live shows near-zero views because the platform hasn't distributed it yet, not because it
    flopped, and would just be noise (or actively misleading) here."""
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

    now = datetime.now(timezone.utc)
    scored = [
        e for e in memory.values()
        if isinstance(e, dict) and _entry_has_metrics(e) and _old_enough_for_viral_signal(e, now)
    ]
    if not scored:
        return ""

    lines = [
        f"- \"{e.get('title', '?')}\" (viral_score={e.get('viral_score', '?')}, "
        f"energy={e.get('energy_rating', '?')}): {_format_platform_metrics(e)}"
        for e in scored
    ]
    logger.info("Loaded %d viral performance data point(s) from %s", len(lines), VIRAL_MEMORY_PATH)
    return (
        "\n\nVIRAL PERFORMANCE DATA (real per-platform metrics for previously uploaded clips — "
        "cross-reference this with the clips you're scoring now; a similar topic/energy/"
        "visual style to a past winner should push reward_score up and may earn a "
        "viral_pattern_rule, a similar style to a past flop should push it down):\n"
        + "\n".join(lines)
    )


# reward_score/viral_score threshold for treating an uploaded-but-not-yet-measured clip as a
# positive example when no real TikTok performance data exists yet to judge it by.
ACCEPTED_CLIP_MIN_VIRAL_SCORE = 7


def load_accepted_clips_section() -> str:
    """Fallback signal for viral_pattern_rule generation when load_viral_memory_section() has
    nothing (2026-08-19): real TikTok view/like counts take real time to accumulate past
    VIRAL_SIGNAL_MIN_AGE_HOURS, but a clip the critic itself already scored highly and that
    made it all the way to a real, confirmed publish is still a meaningful — if weaker —
    signal about what's working, worth reinforcing before real performance data exists to
    confirm or deny it. Reads uploaded_clips/*.json directly (not through auto_pilot.py, to
    avoid a circular import: auto_pilot already imports this module) rather than
    viral_memory.json, since a just-uploaded clip may not have a viral_memory.json entry at
    all yet (metrics_tracker.py hasn't polled it once). Only ever used as a fallback — real
    performance data is always the stronger, preferred signal when it exists."""
    if not UPLOADED_CLIPS_DIR.exists():
        return ""

    accepted = []
    for path in sorted(UPLOADED_CLIPS_DIR.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("confirmed") and (entry.get("viral_score") or 0) >= ACCEPTED_CLIP_MIN_VIRAL_SCORE:
            accepted.append(entry)

    if not accepted:
        return ""

    lines = [
        f"- \"{e.get('title', '?')}\" (viral_score={e.get('viral_score', '?')}, "
        f"energy={e.get('energy_rating', '?')})"
        for e in accepted
    ]
    logger.info(
        "Loaded %d accepted-clip data point(s) from %s (no real viral performance data available yet)",
        len(lines), UPLOADED_CLIPS_DIR,
    )
    return (
        "\n\nACCEPTED CLIPS DATA (no real TikTok view/like data available yet — these are "
        "clips that already scored highly with the critic and were successfully published; "
        "a weaker signal than real performance data, but a recurring topic/energy/style "
        "pattern among them is still worth noticing):\n" + "\n".join(lines)
    )


# How far back before a clip's own start_time to pull lead-in transcript text for the critic
# (see extract_clip_context_text). A short reaction/punchline clip legitimately starts right
# at the payoff with no setup of its own — judged on its own isolated transcript slice alone,
# that reads to the critic as "confusing, no context, starts mid-conversation" even when it's
# a perfectly good clip. 20s is enough to show what the reaction is reacting TO without
# dumping in unrelated earlier material.
CRITIC_CONTEXT_LOOKBACK_SECONDS = 20.0


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


def extract_clip_context_text(
    transcript: Optional[dict], start: float, lookback: float = CRITIC_CONTEXT_LOOKBACK_SECONDS,
) -> str:
    """The transcript text spoken in the `lookback` seconds immediately BEFORE a clip's own
    start_time — background only, never part of the clip itself. Lets the critic tell "this
    reaction has no setup because the AI cut it badly" apart from "this reaction has no setup
    because it's a short, self-contained punchline reacting to something just before it" —
    the latter is normal, common Twitch content and shouldn't be scored down for it."""
    if not transcript:
        return ""

    segments = transcript.get("segments", [])
    lead_in = [
        seg["text"] for seg in segments
        if seg["end"] > start - lookback and seg["start"] < start
    ]
    return " ".join(lead_in).strip()


def _read_image_bytes(path: Path) -> Optional[bytes]:
    """Raw JPEG bytes for genai_types.Part.from_bytes() — Gemini's contents list takes
    Part objects directly, no base64 data-URI wrapping needed (that was an OpenAI
    image_url-specific requirement)."""
    try:
        return path.read_bytes()
    except OSError as e:
        logger.warning("Could not read frame %s for vision critic: %s", path, e)
        return None


def extract_frames_for_clips(clips: List[dict], rendered: Optional[Dict[str, Path]]) -> Dict[str, List[Path]]:
    """Best-effort frame extraction for every clip that has a known rendered .mp4 path.
    `rendered` (clip_title -> output_path) is normally supplied by auto_pilot.py, which
    just rendered these clips; when called standalone (train_loop.py's CLI), falls back to
    matching output/clip_<index>_*.mp4 by position within the clips list (via
    process_module.clip_glob_pattern() — the one shared definition of that filename format,
    also used by process.render_clip() to write it). Returns {} — not an exception — if
    nothing could be extracted, so the caller can degrade to text-only."""
    resolved_paths: Dict[str, Path] = {}

    for i, clip in enumerate(clips, start=1):
        title = clip.get("title", "")
        output_path = (rendered or {}).get(title)

        if output_path is None:
            candidates = sorted(process_module.OUTPUT_DIR.glob(process_module.clip_glob_pattern(i)))
            output_path = candidates[0] if candidates else None

        if output_path is not None and Path(output_path).exists():
            resolved_paths[title] = Path(output_path)

    # Each clip's 3-frame extraction is independent I/O-bound ffmpeg work against its own
    # rendered file — running the whole batch concurrently (not just the 3 frames within one
    # clip, handled inside process.extract_preview_frames itself) cuts wall-clock time
    # roughly in proportion to batch size instead of paying for every clip serially.
    frames_by_title: Dict[str, List[Path]] = {}
    if not resolved_paths:
        return frames_by_title

    with ThreadPoolExecutor(max_workers=min(8, len(resolved_paths))) as pool:
        futures = {
            title: pool.submit(process_module.extract_preview_frames, path)
            for title, path in resolved_paths.items()
        }
        for title, future in futures.items():
            frames = future.result()
            if frames:
                frames_by_title[title] = frames

    return frames_by_title


def build_critic_user_content(
    clips: List[dict],
    transcript: Optional[dict],
    feedback_by_title: Dict[str, List[str]],
    frames_by_title: Dict[str, List[Path]],
) -> List[Union[str, "genai_types.Part"]]:
    """Builds the multimodal contents list: plain strings interleaved with
    genai_types.Part image parts (Gemini's contents list accepts a flat mix of both
    directly), so the model can visually attribute images to the right clip."""
    content: List[Union[str, genai_types.Part]] = ["Bewerte die folgenden Clips:"]

    for idx, clip in enumerate(clips, start=1):
        title = clip.get("title", "Untitled")
        feedback = feedback_by_title.get(title)
        context_text = extract_clip_context_text(transcript, clip["start_time"])
        block = (
            f"\n--- Clip {idx}: {title} ---\n"
            f"Hook-Begründung (vom Auswahl-Modell): {clip.get('hook_explanation', '')}\n"
        )
        if context_text:
            block += (
                f"Kontext DAVOR (nicht Teil des Clips — nur zum Verständnis, was der Clip "
                f"aufgreift): {context_text}\n"
            )
        block += (
            f"Transkript im Clip-Zeitbereich: "
            f"{extract_clip_transcript_text(transcript, clip['start_time'], clip['end_time'])}"
        )
        if feedback:
            block += "\nMenschliches Feedback zu diesem Clip: " + " | ".join(feedback)
        content.append(block)

        for frame_path in frames_by_title.get(title, []):
            image_bytes = _read_image_bytes(frame_path)
            if image_bytes:
                content.append(genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

    return content


def _call_critic(content: List[Union[str, "genai_types.Part"]], model: str) -> CriticBatch:
    client = genai.Client()
    # Accepted-clips data is only ever a fallback (2026-08-19) — real performance data is
    # always the stronger, preferred signal when it exists.
    viral_section = load_viral_memory_section() or load_accepted_clips_section()
    if viral_section:
        content = content + [viral_section]

    # `model` (caller-chosen: vision_model or text_model, see run_critic() below) always goes
    # first; the rest of llm_utils.DEFAULT_MODEL_POOL follows as fallback if it's quota-
    # exhausted for today — see llm_utils.call_with_fallback's own docstring.
    model_pool = [model] + [m for m in llm_utils.DEFAULT_MODEL_POOL if m != model]

    response = llm_utils.call_with_fallback(
        lambda m: client.models.generate_content(
            model=m,
            contents=content,
            config=genai_types.GenerateContentConfig(
                system_instruction=CRITIC_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=CriticBatch,
                max_output_tokens=MAX_COMPLETION_TOKENS,
            ),
        ),
        description=f"train_loop._call_critic({model})",
        model_pool=model_pool,
    )

    candidate = response.candidates[0] if response.candidates else None
    finish_reason = candidate.finish_reason if candidate else None
    parsed = response.parsed
    if parsed is None:
        logger.warning(
            "Critic response could not be parsed (finish_reason=%s) — no guidelines will be "
            "derived from this run.", finish_reason,
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
        logger.warning(
            "Vision critic failed (%s) — falling back to text-only evaluation",
            llm_utils.redact_secrets(str(e)),
        )

    content = build_critic_user_content(clips, transcript, feedback_by_title, {})
    logger.info("Sending %d clip(s) to the text-only critic (%s)", len(clips), text_model)
    try:
        return _call_critic(content, text_model)
    except Exception as e:
        logger.error("Critic LLM call failed (text-only fallback): %s", llm_utils.redact_secrets(str(e)))
        raise


def filter_verdicts_to_known_clips(batch: CriticBatch, clips: List[dict]) -> CriticBatch:
    """Structured-output validation only confirms reward_score's type/range — nothing
    upstream ever confirmed a verdict's clip_title actually corresponds to a real clip in the
    batch the critic was asked to judge. A hallucinated or mistyped clip_title would silently
    vanish from every title-keyed lookup downstream (purge_low_scoring_clips's score lookup,
    the guideline-generation loop below) with no error — it just quietly never gets applied.
    This drops any verdict whose clip_title isn't an exact match for a clip actually in this
    batch, and any second verdict for a title already seen (keeping the first), logging each
    one so a systematic mismatch is visible instead of silent."""
    known_titles = {clip.get("title", "") for clip in clips}
    seen_titles: set = set()
    kept = []

    for verdict in batch.verdicts:
        if verdict.clip_title not in known_titles:
            logger.warning(
                "Critic verdict for '%s' doesn't match any clip in this batch — dropping it "
                "instead of letting it silently vanish from every title-keyed lookup.",
                verdict.clip_title,
            )
            continue
        if verdict.clip_title in seen_titles:
            logger.warning(
                "Critic returned a second verdict for '%s' — keeping the first, dropping the duplicate.",
                verdict.clip_title,
            )
            continue
        seen_titles.add(verdict.clip_title)
        kept.append(verdict)

    return CriticBatch(verdicts=kept)


# --- ai_guidelines.txt: three categorized sections ------------------------------------------

CATEGORY_HEADERS = {
    "content_positive": "[+] POSITIVE REWARDS (DO THIS):",
    "content_negative": "[-] PENALTIES (NEVER DO THIS):",
    "visual_positive": "[VISUAL+] VISUAL COMPOSITION — DO THIS:",
    "visual_negative": "[VISUAL-] VISUAL COMPOSITION — NEVER DO THIS:",
    "viral_patterns": "[VIRAL] SUCCESS PATTERNS (FROM PERFORMANCE DATA):",
}
CATEGORY_ORDER = ["content_positive", "content_negative", "visual_positive", "visual_negative", "viral_patterns"]

# ai_guidelines.txt is a pure accumulator with nothing pruning it — months of continuous
# operation means an ever-larger block of text injected into every future analyze.py/critic
# prompt, including entries that may no longer be relevant. Capping each category keeps the
# prompt bounded; trimming from the front (oldest) keeps the most recently learned rules,
# which is what should win when an old rule and a new one implicitly conflict.
MAX_RULES_PER_CATEGORY = 40


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


def _cap_rules(rules: List[str], max_count: int = MAX_RULES_PER_CATEGORY) -> List[str]:
    """Keeps only the most recent `max_count` rules — rules are appended in order (existing
    first, newly learned ones last), so trimming from the front drops the oldest entries and
    keeps what was learned most recently."""
    if len(rules) <= max_count:
        return rules
    dropped = len(rules) - max_count
    logger.info("Trimming %d oldest rule(s) to stay within the %d-per-category cap", dropped, max_count)
    return rules[-max_count:]


def save_guidelines(categories: Dict[str, List[str]]) -> None:
    lines: List[str] = []
    for key in CATEGORY_ORDER:
        lines.append(CATEGORY_HEADERS[key])
        rules = categories.get(key) or []
        lines += [f"- {rule}" for rule in rules] if rules else ["- (noch keine)"]
        lines.append("")

    atomic_io.atomic_write_text(analyze.AI_GUIDELINES_PATH, "\n".join(lines).rstrip() + "\n")
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
    batch = filter_verdicts_to_known_clips(batch, clips)

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
        "content_positive": _cap_rules(_dedupe(existing["content_positive"] + new_content_positive)),
        "content_negative": _cap_rules(_dedupe(existing["content_negative"] + new_content_negative)),
        "visual_positive": _cap_rules(_dedupe(existing["visual_positive"] + new_visual_positive)),
        "visual_negative": _cap_rules(_dedupe(existing["visual_negative"] + new_visual_negative)),
        "viral_patterns": _cap_rules(_dedupe(existing["viral_patterns"] + new_viral_patterns)),
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
