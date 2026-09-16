"""Face detection: locate the facecam region in a video frame for dynamic 9:16 cropping."""

import logging
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.core.base_options import BaseOptions

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "blaze_face_short_range.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)

# How much to expand the raw face bounding box so head, shoulders and some background are
# visible instead of just the face. Empirical, not derived from anything — chosen by
# rendering test clips and eyeballing the crop at a handful of values (2.0 felt like a tight
# passport-photo crop; 3.0 pulled in distracting background). No documented basis beyond
# that, so treat re-tuning the same way: render a few real clips at the new value and look
# at them before trusting it. Raise it if facecams look too zoomed-in/cropped-at-the-chin;
# lower it if too much background/empty space surrounds the face.
PADDING_FACTOR = 2.5

# Below this width/height (px), a padded box is considered degenerate (e.g. a face detected
# right at the frame edge, clamped down to almost nothing) and we fall back. Empirical, same
# basis (or lack of one) as PADDING_FACTOR above — 100px was small enough to only catch
# genuinely-clamped edge cases at typical source resolutions (1080p+), not real small-but-
# valid faces. If source videos are ever rendered at much lower resolution, this threshold
# would need to scale down with them or it'll start rejecting legitimate detections.
MIN_BOX_DIMENSION = 100

# 2026-08-19: a real single-webcam clip got auto-laid-out as blur_background instead of
# split_screen — traced to gameplay HUD elements (character portraits, kill-cam faces, UI
# icons) tripping FaceDetectorOptions' own baseline confidence floor (0.5) often enough to be
# counted as a second "face" alongside the real streamer webcam, which pushed
# process.resolve_layout() into its face_count != 1 fallback path for a clip that only ever
# had one real person in frame. Detections below this higher bar still show up in has_face()
# and get_facecam_coordinates()'s crop-box choice (better to crop toward a low-confidence real
# face than nothing), but are excluded from the face COUNT the split_screen/full_cam/
# blur_background decision is keyed on — a HUD portrait passing the detector's 0.5 floor
# rarely also clears 0.65.
FACE_COUNT_MIN_CONFIDENCE = 0.65

# get_facecam_coordinates() trusts ANY dynamic detection at/above this confidence over a
# streamer's static facecam_box override — streamers move/resize their webcam overlay between
# streams, so a rigid static box that always wins would silently crop the wrong region once a
# cam moves. 0.50 matches MediaPipe FaceDetectorOptions' own baseline detection floor (see
# FACE_COUNT_MIN_CONFIDENCE's docstring above), so in practice any detection this module
# returns at all already clears it — the override is therefore only a last-resort fallback for
# the genuine zero-faces-across-every-sampled-frame case (streamer's cam is off-model, oddly
# angled, or the detector otherwise finds nothing at all), never a way to override a real,
# just-lower-confidence detection. 2026-08-23 account-owner spec: previously this compared
# against a much higher 0.75 bar, which meant a real but marginal detection (motion blur, an
# odd angle) still lost to the static box even though dynamic detection HAD found the actual
# (possibly relocated) camera — exactly the failure mode this lower bar exists to avoid.
DYNAMIC_DETECTION_MIN_CONFIDENCE = 0.50

# How many frames to sample across a clip's duration for get_facecam_coordinates()'s temporal
# smoothing (see _sample_smoothed_box below) — high enough that one bad frame (a blink, motion
# blur, a HUD element briefly overlapping the webcam) can't dominate the median, low enough
# that a several-minute clip doesn't cost many extra decode+detect passes just to place one
# crop box. Odd, so the median of the sampled x/y/w/h values is always an actually-observed
# component-wise combination rather than an average of two straddling samples.
FACECAM_SMOOTH_SAMPLE_COUNT = 5


def _ensure_model() -> Path:
    if MODEL_PATH.exists():
        return MODEL_PATH

    MODEL_DIR.mkdir(exist_ok=True)
    logger.info("Downloading face detection model to %s", MODEL_PATH)
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    except Exception as e:
        logger.error("Failed to download face detection model: %s", e)
        raise
    return MODEL_PATH


FALLBACK_CORNER = "top_right"  # or "top_left" — where streamer webcams classically sit


def _fallback_box(frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Top-left or top-right screen quadrant, used when no face is detected.

    Streamer webcams classically sit in a corner of the frame, not smeared across
    the full width — a full-width slice cover-fit into a near-square split-screen
    half forces an extreme zoom (the "flat"/broken-looking facecam half). A single
    quadrant keeps a moderate, corner-anchored aspect close to a normal webcam.
    """
    w = frame_w // 2
    h = frame_h // 2
    x = frame_w - w if FALLBACK_CORNER == "top_right" else 0
    return x, 0, w, h


def _detect_faces(
    video_path: str, timestamp: float
) -> tuple[list[tuple[int, int, int, int]], list[float], int, int]:
    """Read the frame at `timestamp` and return (all_raw_boxes, all_scores, frame_w,
    frame_h) — every face MediaPipe found, not just the first, alongside each one's
    confidence score. Previously this only ever kept result.detections[0] and discarded the
    count (and score) entirely, so there was no way to tell "one face" from "several faces",
    or a confident detection from a marginal one, anywhere in this module (found in review,
    2026-08-19, needed for the full-cam/split-screen/blur-background layout decision below)."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        success, frame = cap.read()
        if not success or frame is None:
            raise RuntimeError(f"Could not read frame at {timestamp:.2f}s from {video_path}")
        frame_h, frame_w = frame.shape[:2]
    finally:
        cap.release()

    model_path = _ensure_model()
    options = mp_vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.IMAGE,
    )

    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

    with mp_vision.FaceDetector.create_from_options(options) as detector:
        result = detector.detect(mp_image)

    boxes = [
        (d.bounding_box.origin_x, d.bounding_box.origin_y, d.bounding_box.width, d.bounding_box.height)
        for d in result.detections
    ]
    scores = [
        d.categories[0].score if d.categories else 0.0
        for d in result.detections
    ]
    return boxes, scores, frame_w, frame_h


def has_face(video_path: str, timestamp: float) -> bool:
    """Cheap presence check."""
    boxes, _, _, _ = _detect_faces(video_path, timestamp)
    return len(boxes) > 0


def detect_faces_for_layout(video_path: str, timestamp: float) -> tuple[int, Optional[tuple[int, int, int, int]], int, int]:
    """One detection pass covering everything process.resolve_layout() needs to pick between
    full_cam / split_screen / blur_background: (face_count, primary_raw_box_or_None, frame_w,
    frame_h). A single call instead of running detection twice (once for a presence/count
    check, once for the box) — the raw (unpadded) box lets the caller measure how much of
    the frame the face actually covers via face_area_ratio() below.

    face_count only counts detections at/above FACE_COUNT_MIN_CONFIDENCE — a gameplay HUD
    element (character portrait, kill-cam face, UI icon) can clear MediaPipe's own baseline
    0.5 floor often enough to be miscounted as a second person and wrongly push a genuine
    single-webcam clip into the blur_background fallback (found live, 2026-08-19); the
    primary box is still whichever detection scored highest overall (real face or not), so a
    confident low-scoring true face is still cropped toward rather than discarded."""
    boxes, scores, frame_w, frame_h = _detect_faces(video_path, timestamp)
    if not boxes:
        return 0, None, frame_w, frame_h

    primary_index = max(range(len(boxes)), key=lambda i: scores[i])
    face_count = sum(1 for s in scores if s >= FACE_COUNT_MIN_CONFIDENCE)
    return face_count, boxes[primary_index], frame_w, frame_h


def face_area_ratio(face_box: tuple[int, int, int, int], frame_w: int, frame_h: int) -> float:
    """Fraction of the frame's area the RAW (unpadded) detected face bounding box covers —
    used to tell a close-up/full-cam subject (large face-to-frame ratio: a Just-Chatting-
    style stream where the whole source frame IS the person) apart from a small corner
    webcam box over a separate gameplay feed (traditional split-screen streaming layout).
    Both are "exactly one face" by count alone, so count can't distinguish them — this can."""
    _, _, w, h = face_box
    frame_area = frame_w * frame_h
    if frame_area <= 0:
        return 0.0
    return (w * h) / frame_area


def face_center_x_ratio(face_box: tuple[int, int, int, int], frame_w: int) -> float:
    """Horizontal center of the face box as a fraction of frame width (0 = left edge, 1 =
    right edge) — the position signal used alongside face_area_ratio() to tell a genuine
    corner gameplay facecam (pushed to an edge) apart from a wide/distant Just-Chatting shot
    (small on-screen, but still horizontally centered)."""
    x, _, w, _ = face_box
    if frame_w <= 0:
        return 0.5
    return (x + w / 2) / frame_w


def padding_factor_from_margin(margin_fraction: float) -> float:
    """Convert a padding margin expressed as "extra fraction of the box's own size added on
    each side" (e.g. 0.25 for a 25% margin — the account-owner spec's "20-30% extra margin
    around head/shoulders") into the multiplicative `padding_factor` get_facecam_coordinates()
    expects. get_facecam_coordinates() pads by size * (padding_factor - 1) / 2 per side, i.e.
    a margin_fraction of the box size, so padding_factor = 1 + 2 * margin_fraction."""
    return 1 + 2 * margin_fraction


def _ratio_box_to_pixels(
    ratio_box: Sequence[float], frame_w: int, frame_h: int
) -> tuple[int, int, int, int]:
    """Convert a streamers.json `facecam_box` — [x1, y1, x2, y2] as fractions (0..1) of frame
    width/height, resolution-independent since source recordings vary in size across chunks —
    into a pixel (x, y, w, h) box for this specific frame's dimensions."""
    x1, y1, x2, y2 = ratio_box
    x1_px = max(0, min(frame_w, int(x1 * frame_w)))
    y1_px = max(0, min(frame_h, int(y1 * frame_h)))
    x2_px = max(0, min(frame_w, int(x2 * frame_w)))
    y2_px = max(0, min(frame_h, int(y2 * frame_h)))
    return x1_px, y1_px, max(1, x2_px - x1_px), max(1, y2_px - y1_px)


def _frame_size(video_path: str) -> tuple[int, int]:
    """(width, height) straight off the container's own metadata — no frame decode, unlike
    _detect_faces(). Lets a caller that already KNOWS which region it wants (a ratio box) turn
    it into pixels without paying for a detection pass. (0, 0) when unreadable."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0, 0
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _sample_smoothed_box(
    video_path: str, start_ts: float, end_ts: float, sample_count: int
) -> tuple[Optional[tuple[int, int, int, int]], float, int, int]:
    """Sample `sample_count` frames evenly spaced across [start_ts, end_ts] (a clip's own
    duration, not just its first frame) and return a temporally-smoothed
    (median_box_or_None, mean_confidence, frame_w, frame_h).

    Averaging/smoothing across multiple sampled frames instead of trusting a single static
    frame coordinate: a lone unlucky sample (a blink, motion blur, a HUD element briefly
    overlapping the webcam) can otherwise anchor the ENTIRE clip's crop, since
    get_facecam_coordinates() only ever detects once per clip. The median of each of x/y/w/h
    independently (not a mean) is deliberate — it discards a single outlier sample's pull on
    the result the way a mean would still be dragged by it, while still returning one stable
    box for the whole clip's render (no per-frame jitter within the render itself, since this
    box is still used as one static crop, same as before).

    Every sampled frame's OWN highest-scoring detection is used (not a fixed confidence floor)
    so a sample with no detection at all just contributes nothing rather than forcing a
    low-quality box into the median; frames where the streamer briefly isn't in view (e.g.
    stood up, camera glitch) don't distort the result as long as most samples still see them.
    """
    if sample_count <= 1 or end_ts <= start_ts:
        timestamps = [start_ts]
    else:
        step = (end_ts - start_ts) / (sample_count - 1)
        timestamps = [start_ts + i * step for i in range(sample_count)]

    boxes: list[tuple[int, int, int, int]] = []
    scores: list[float] = []
    frame_w = frame_h = 0
    for ts in timestamps:
        try:
            raw_boxes, raw_scores, frame_w, frame_h = _detect_faces(video_path, ts)
        except RuntimeError as e:
            logger.warning("Facecam smoothing: could not read frame at %.2fs from %s (%s), skipping sample", ts, video_path, e)
            continue
        if not raw_boxes:
            continue
        best = max(range(len(raw_boxes)), key=lambda i: raw_scores[i])
        boxes.append(raw_boxes[best])
        scores.append(raw_scores[best])

    if not boxes:
        return None, 0.0, frame_w, frame_h

    median_box = (
        int(_median([b[0] for b in boxes])),
        int(_median([b[1] for b in boxes])),
        int(_median([b[2] for b in boxes])),
        int(_median([b[3] for b in boxes])),
    )
    mean_confidence = sum(scores) / len(scores)
    logger.info(
        "Facecam smoothing over [%.2fs, %.2fs]: %d/%d samples had a face, median=%s, mean_confidence=%.2f",
        start_ts, end_ts, len(boxes), len(timestamps), median_box, mean_confidence,
    )
    return median_box, mean_confidence, frame_w, frame_h


def get_facecam_coordinates(
    video_path: str,
    timestamp: float,
    padding_factor: float = PADDING_FACTOR,
    clip_end: Optional[float] = None,
    sample_count: int = FACECAM_SMOOTH_SAMPLE_COUNT,
    override_box_ratio: Optional[Sequence[float]] = None,
    verified_box_ratio: Optional[Sequence[float]] = None,
) -> tuple[int, int, int, int]:
    """Detect a face at `timestamp` seconds into `video_path` and return a padded (x, y, w, h) box in pixels.

    That single box is used as a static crop for the whole clip's render — so there is no
    per-frame jitter or flicker within a clip by construction (no frame-by-frame re-detection
    to disagree with itself). When `clip_end` is given (and after `timestamp`), the box itself
    is no longer read off a single frame: it's the temporally-smoothed median of
    FACECAM_SMOOTH_SAMPLE_COUNT frames sampled across [timestamp, clip_end] — see
    _sample_smoothed_box's own docstring for why. Without `clip_end`, this falls back to the
    original single-frame read (used by callers, and tests, that only care about one instant).

    `padding_factor` defaults to PADDING_FACTOR (tuned for split_screen's top-third zone);
    process.py passes a larger value for full_cam, where the same box gets stretched to fill
    the ENTIRE tall 9:16 canvas instead of a short-wide strip — cover-fitting a padded box
    tuned for a short zone into a much taller one crops far tighter than intended. Callers can
    also derive a padding_factor from a plain "20-30% margin" fraction via
    padding_factor_from_margin() instead of picking a multiplier directly.

    `override_box_ratio`, when given (streamers.json's per-streamer `facecam_box`, a
    resolution-independent [x1, y1, x2, y2] fraction box), is used ONLY when the dynamic
    detection is missing or its confidence is below DYNAMIC_DETECTION_MIN_CONFIDENCE — a
    genuine detection (the streamer's ACTUAL, possibly-moved camera) always wins over a stale
    hand-picked box; the override exists purely as a loose last-resort for the case where
    detection finds nothing at all. The override is used as-is (no extra padding applied):
    it's already an explicit crop box, not a raw face box that needs headroom added around it.

    `verified_box_ratio`, when given, WINS over everything below including per-clip dynamic
    detection — it is this chunk's persistent webcam position from locate_persistent_facecam()
    (see its docstring), i.e. the same detector's verdict over many more frames plus a
    positional-consistency requirement no single clip's detection can provide. Outranking
    dynamic detection is the entire point: the case this exists for is exactly the one where a
    frame's HIGHEST-scoring face is not the camera at all (a game-rendered portrait), so
    deferring to per-clip confidence there would just reproduce the bug. Unlike
    `override_box_ratio` this IS a raw face box, so it gets the same `padding_factor` headroom
    a live detection would.
    """
    if verified_box_ratio is not None:
        frame_w, frame_h = _frame_size(video_path)
        if frame_w > 0 and frame_h > 0:
            verified_raw = _ratio_box_to_pixels(verified_box_ratio, frame_w, frame_h)
            verified_padded = _pad_and_clamp_box(verified_raw, frame_w, frame_h, padding_factor)
            if verified_padded is not None:
                logger.info(
                    "Facecam: using this chunk's verified persistent webcam position raw=%s padded=%s",
                    verified_raw, verified_padded,
                )
                return verified_padded
            logger.warning(
                "Facecam: verified persistent box %s clamped down to nothing — falling through "
                "to per-clip detection", verified_raw,
            )
        else:
            logger.warning(
                "Facecam: could not read frame size for %s — falling through to per-clip "
                "detection instead of the verified persistent box", video_path,
            )

    smoothed = clip_end is not None and clip_end > timestamp
    if smoothed:
        raw_box, confidence, frame_w, frame_h = _sample_smoothed_box(video_path, timestamp, clip_end, sample_count)
    else:
        boxes, scores, frame_w, frame_h = _detect_faces(video_path, timestamp)
        if boxes:
            best = max(range(len(boxes)), key=lambda i: scores[i])
            raw_box, confidence = boxes[best], scores[best]
        else:
            raw_box, confidence = None, 0.0

    where = f"smoothed over [{timestamp:.2f}s, {clip_end:.2f}s]" if smoothed else f"at {timestamp:.2f}s"

    if override_box_ratio is not None and (raw_box is None or confidence < DYNAMIC_DETECTION_MIN_CONFIDENCE):
        logger.info(
            "Facecam %s: dynamic confidence=%.2f (< %.2f threshold or no detection) — using streamer's "
            "static facecam override instead", where, confidence, DYNAMIC_DETECTION_MIN_CONFIDENCE,
        )
        return _ratio_box_to_pixels(override_box_ratio, frame_w, frame_h)

    if raw_box is None:
        logger.warning("No face detected (%s) in %s, using fallback crop", where, video_path)
        return _fallback_box(frame_w, frame_h)

    padded = _pad_and_clamp_box(raw_box, frame_w, frame_h, padding_factor)
    if padded is None:
        logger.warning(
            "Degenerate face box at %.2fs (raw=%s) after clamping to frame edges, using fallback crop",
            timestamp, raw_box,
        )
        return _fallback_box(frame_w, frame_h)

    logger.info(
        "Face detected (%s): raw=%s padded=%s", where, raw_box, padded,
    )
    return padded


def _pad_and_clamp_box(
    raw_box: tuple[int, int, int, int], frame_w: int, frame_h: int, padding_factor: float,
) -> Optional[tuple[int, int, int, int]]:
    """Shared by get_facecam_coordinates() (one box) and get_multi_cam_coordinates() (one box
    per grid tile) — adds `padding_factor`'s headroom around a raw MediaPipe box and clamps to
    the frame edges, exactly the math get_facecam_coordinates() always did inline. Returns None
    (not a raised error) for a box that clamps down to near-nothing (a raw box hard against a
    frame edge with padding pushing the rest out of bounds) — same fail-open-to-fallback
    posture as every other degenerate-box case in this module."""
    x, y, w, h = raw_box
    pad_w = w * (padding_factor - 1) / 2
    pad_h = h * (padding_factor - 1) / 2

    x1 = max(0, int(x - pad_w))
    y1 = max(0, int(y - pad_h))
    x2 = min(frame_w, int(x + w + pad_w))
    y2 = min(frame_h, int(y + h + pad_h))

    final_w, final_h = x2 - x1, y2 - y1
    if final_w < MIN_BOX_DIMENSION or final_h < MIN_BOX_DIMENSION:
        return None
    return x1, y1, final_w, final_h


# Multi-cam / collab-grid scene detection (2026-08-24 incident: a Discord call, podcast, or
# multi-participant "Craft Attack"-style grid shows several roughly-equal-sized face tiles
# spread across the frame, not one dominant streamer face over a separate gameplay/desktop
# region. Forcing the existing 2-slot split_screen (crop toward ONE face, treat the rest of
# the frame as "gameplay") onto this scene produced exactly what was observed live: a sliver
# of one tile, hundreds of pixels of black, then another tile — the "gameplay" half was itself
# just another region of the SAME grid, straddling the black gaps built into the grid layout.
MULTI_CAM_MIN_FACES = 2
MULTI_CAM_MAX_FACES = 4
# Two tiles in the same grid render at close to the same size — unlike a real streamer cam
# next to a small/large false-positive (a HUD icon, a face within reacted photo/video
# content), whose area ratio to the real face tends to be far more lopsided.
MULTI_CAM_MAX_AREA_RATIO_SPREAD = 3.0


# Fraction of near-black pixels a facecam crop region can contain before it's treated as
# landing on empty/gap space rather than real content. Not 0 -- a real webcam feed with a dark
# background (a dim room, a black hoodie filling part of frame) legitimately has SOME black
# pixels; this only needs to catch a crop that's PREDOMINANTLY blank.
BOX_BLACK_PIXEL_THRESHOLD = 20
# Tuned against the actual 2026-08-24 incident render: a pixel-sampled vertical scan of the
# defective output measured ~44% near-black pixels within the face zone alone (worse combined
# with the adjoining game-zone black run past it) — a 0.5 bar would have let that exact case
# through. 0.3 catches it with headroom while still tolerating a genuinely dim real webcam
# shot (a dark room, a black hoodie filling part of frame), which doesn't typically run this high.
BOX_MAX_BLACK_FRACTION = 0.3


def box_is_mostly_black(
    video_path: str, timestamp: float, box: tuple[int, int, int, int],
    max_black_fraction: float = BOX_MAX_BLACK_FRACTION,
) -> bool:
    """Direct pixel check on the ACTUAL frame content a facecam_box (dynamic detection or a
    streamer's static override) would crop toward — catches the exact defect observed live,
    2026-08-24: a collab/grid scene's own black margins or gaps between tiles happening to sit
    where a streamer's SOLO-layout webcam normally does, producing a mostly-black slot instead
    of a real facecam no matter how well-reasoned the box itself is. General and scene-agnostic
    by design — unlike face-count-based heuristics (is_multi_cam_scene,
    looks_like_busy_multi_face_scene), this doesn't need to know WHY a box would be black, only
    THAT it is, so it also catches collab formats too visually different from every case those
    heuristics were tuned against. Fails open (returns False — "trust it") on any read error,
    same posture as every other layout signal in this module: a box this can't check is not
    treated as evidence against itself."""
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return False
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            success, frame = cap.read()
        finally:
            cap.release()
        if not success or frame is None:
            return False
        x, y, w, h = box
        region = frame[max(0, y):y + h, max(0, x):x + w]
        if region.size == 0:
            return False
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        black_fraction = float((gray < BOX_BLACK_PIXEL_THRESHOLD).mean())
        return black_fraction > max_black_fraction
    except Exception as e:
        logger.warning("Could not check box blackness at %.2fs in %s (%s) — trusting the box as-is", timestamp, video_path, e)
        return False


def looks_like_busy_multi_face_scene(video_path: str, timestamp: float) -> bool:
    """True when the frame at `timestamp` has 2+ face detections of ANY confidence — not just
    ones clearing FACE_COUNT_MIN_CONFIDENCE. Used only to decide whether a zero-CONFIDENT-face
    reading is a genuine "streamer briefly out of frame" moment (trust their static facecam
    override — its original purpose) or a busy multi-participant scene their solo webcam
    override has no business being cropped toward. Found live, 2026-08-24: a 9-tile Werwolf
    collab grid's individual tiles are too small/low-quality for MediaPipe to confidently clear
    FACE_COUNT_MIN_CONFIDENCE at all (real observed scores: 0.50-0.54, against a 0.65 floor) —
    reading as "zero faces" exactly like an ordinary momentary dropout would, which used to
    send it straight into the static-override split_screen this whole detection exists to
    avoid misapplying."""
    try:
        boxes, _, _, _ = _detect_faces(video_path, timestamp)
    except RuntimeError:
        return False
    return len(boxes) >= 2


def is_multi_cam_scene(
    boxes: Sequence[tuple[int, int, int, int]], scores: Sequence[float],
) -> bool:
    """True when `boxes`/`scores` (as returned by _detect_faces) look like a multi-participant
    camera grid rather than one real streamer face plus incidental/false-positive detections
    elsewhere in the frame. Two signals, both required: (1) MULTI_CAM_MIN_FACES..
    MULTI_CAM_MAX_FACES faces clear FACE_COUNT_MIN_CONFIDENCE — outside that range this is
    either an ordinary single-cam scene or too crowded to confidently split into individual
    slots. (2) the confident faces' areas are all within MULTI_CAM_MAX_AREA_RATIO_SPREAD of
    each other — real grid tiles render at roughly the same size; a single genuine streamer
    face next to a small HUD/game-content false positive does not."""
    confident = [b for b, s in zip(boxes, scores) if s >= FACE_COUNT_MIN_CONFIDENCE]
    if not (MULTI_CAM_MIN_FACES <= len(confident) <= MULTI_CAM_MAX_FACES):
        return False
    areas = [w * h for _, _, w, h in confident]
    if min(areas) <= 0:
        return False
    return max(areas) / min(areas) <= MULTI_CAM_MAX_AREA_RATIO_SPREAD


def detect_multi_cam_faces(
    video_path: str, timestamp: float,
) -> Optional[tuple[list[tuple[int, int, int, int]], int, int]]:
    """One detection pass at `timestamp`: (confident_boxes, frame_w, frame_h) in reading order
    (top-to-bottom, then left-to-right — matching how a grid actually reads on screen) if
    is_multi_cam_scene() confirms this looks like a real multi-participant grid; None otherwise
    (including on a frame-read failure — same fail-open-to-the-existing-single-face-path
    posture as every other layout signal in this module)."""
    try:
        boxes, scores, frame_w, frame_h = _detect_faces(video_path, timestamp)
    except RuntimeError:
        return None
    if not is_multi_cam_scene(boxes, scores):
        return None
    confident = [b for b, s in zip(boxes, scores) if s >= FACE_COUNT_MIN_CONFIDENCE]
    confident.sort(key=lambda b: (b[1], b[0]))  # (y, x) reading order
    return confident, frame_w, frame_h


# --- Persistent facecam localisation ("Facecam-Agent", 2026-08-25) ---------------------------
#
# 2026-08-25 incident: EA FC / FIFA pack-opening clips rendered with the split_screen face zone
# showing a zoomed crop of the GAME (a player card's portrait art) instead of the streamer —
# the same content as the gameplay zone right below it, just zoomed. Two clips shipped like
# this before it was caught.
#
# Every guard in this module missed it, and none of them were wrong to: a player card's
# portrait IS a photograph of a real human face, so MediaPipe scores it as one. face_count is
# 1 (not a grid, not busy, not crowded), the box is bright gold (not black), and during a
# walkout animation the card sits still long enough that _sample_smoothed_box's within-clip
# median is perfectly stable. Every existing signal asks "is there a face, and where" — none
# asks "is this face the STREAMER'S CAMERA, or something the game is drawing".
#
# The discriminator this adds: a webcam overlay is FIXED FURNITURE for the whole broadcast —
# same rectangle, minute after minute, across menus, matches and pack openings alike. Game
# content is transient: a card fills the screen for a few seconds and is gone. So sampling
# across the WHOLE source chunk (not within one clip, which can be entirely one pack-opening
# sequence) and keeping only a face position that PERSISTS separates the two cleanly, with no
# game-specific knowledge and no extra model.
#
# This also fixes the weaker version of the same bug: when a streamer's real webcam IS on
# screen, a big sharp card portrait can out-score their small corner cam in any single frame.
# Scoring by persistence instead of by one frame's confidence picks the camera.
FACECAM_SCAN_SAMPLE_COUNT = 16
# Fraction of successfully-read samples a position must appear in to count as the facecam. A
# real webcam is present in essentially every frame, so this could be much higher; it is kept
# below half deliberately, for the streamer who steps away, leans out of frame, or whose cam
# the detector loses at odd angles. Transient game content sits far below it either way (a
# pack animation is seconds out of a multi-minute chunk).
FACECAM_MIN_PERSISTENCE = 0.45
# How far two detections may sit apart (as a fraction of frame width/height, center-to-center)
# and still count as the same fixed position. Generous enough for a person moving around
# inside their webcam box — the detected FACE moves within the fixed CAMERA rectangle, which
# is the thing actually being located — while far tighter than the jump between a webcam
# corner and centre-screen game content.
FACECAM_CLUSTER_CENTER_TOLERANCE = 0.08
# Two detections whose sizes differ by more than this factor are not the same face, even at
# the same position — guards against a large centre-screen card portrait absorbing a small
# corner webcam into one cluster when they happen to overlap.
FACECAM_CLUSTER_SIZE_TOLERANCE = 2.0


class FacecamScan(NamedTuple):
    """Result of locate_persistent_facecam(). `box_ratio` is None when the scan ran fine but
    found no position persistent enough to be a camera — a real, meaningful answer ("this
    footage has no webcam overlay"), NOT an error, and the thing that stops game content from
    being cropped as a facecam. Distinguish that from a scan that could not run at all via
    `scan_succeeded` (samples_read == 0): an unreadable file is not evidence about anything,
    so callers must fail open there rather than treating it as "no facecam"."""
    box_ratio: Optional[tuple[float, float, float, float]]
    persistence: float
    samples_with_face: int
    samples_read: int
    mean_confidence: float

    @property
    def scan_succeeded(self) -> bool:
        return self.samples_read > 0


def _video_duration_seconds(video_path: str) -> float:
    """Best-effort duration from frame count / fps. Returns 0.0 (never raises) when either is
    missing or nonsensical — some .ts chunk recordings report neither reliably — which callers
    treat as "can't scan a time range", not as a zero-length video."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.0
    try:
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS)
    finally:
        cap.release()
    if not fps or fps <= 0 or not frame_count or frame_count <= 0:
        return 0.0
    return float(frame_count) / float(fps)


def _boxes_are_same_position(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int], frame_w: int, frame_h: int,
) -> bool:
    """Whether two raw face boxes plausibly sit at the same fixed on-screen position — the
    clustering predicate for locate_persistent_facecam(). Compares CENTERS (not corners: a
    face box grows and shrinks as someone leans toward or away from their camera, which moves
    both corners while leaving the center roughly put) plus a size-similarity guard."""
    if frame_w <= 0 or frame_h <= 0:
        return False
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if min(aw, ah, bw, bh) <= 0:
        return False

    a_cx, a_cy = ax + aw / 2, ay + ah / 2
    b_cx, b_cy = bx + bw / 2, by + bh / 2
    if abs(a_cx - b_cx) / frame_w > FACECAM_CLUSTER_CENTER_TOLERANCE:
        return False
    if abs(a_cy - b_cy) / frame_h > FACECAM_CLUSTER_CENTER_TOLERANCE:
        return False

    a_area, b_area = aw * ah, bw * bh
    larger, smaller = max(a_area, b_area), min(a_area, b_area)
    return larger / smaller <= FACECAM_CLUSTER_SIZE_TOLERANCE


def _scan_frames_for_faces(
    video_path: str, timestamps: Sequence[float],
) -> tuple[list[list[tuple[int, int, int, int]]], list[list[float]], int, int]:
    """Detect faces at each timestamp, reusing ONE VideoCapture and ONE FaceDetector across all
    of them — unlike _detect_faces(), which opens and tears down both per call. At
    FACECAM_SCAN_SAMPLE_COUNT samples that difference is the bulk of the scan's cost (model
    load dominates a single detect), which is what makes a whole-chunk scan cheap enough to run
    once per chunk. Frames that can't be read are skipped, not raised on: the returned lists
    only contain successfully-read samples, so the caller's persistence denominator counts real
    evidence rather than punishing a position for frames nobody could look at."""
    per_frame_boxes: list[list[tuple[int, int, int, int]]] = []
    per_frame_scores: list[list[float]] = []
    frame_w = frame_h = 0

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return per_frame_boxes, per_frame_scores, frame_w, frame_h

    model_path = _ensure_model()
    options = mp_vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.IMAGE,
    )

    try:
        with mp_vision.FaceDetector.create_from_options(options) as detector:
            for ts in timestamps:
                cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
                success, frame = cap.read()
                if not success or frame is None:
                    continue
                frame_h, frame_w = frame.shape[:2]
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                per_frame_boxes.append([
                    (d.bounding_box.origin_x, d.bounding_box.origin_y,
                     d.bounding_box.width, d.bounding_box.height)
                    for d in result.detections
                ])
                per_frame_scores.append([
                    d.categories[0].score if d.categories else 0.0 for d in result.detections
                ])
    finally:
        cap.release()

    return per_frame_boxes, per_frame_scores, frame_w, frame_h


def locate_persistent_facecam(
    video_path: str,
    sample_count: int = FACECAM_SCAN_SAMPLE_COUNT,
    min_persistence: float = FACECAM_MIN_PERSISTENCE,
) -> FacecamScan:
    """Find the streamer's actual webcam overlay in `video_path` by looking for a face position
    that PERSISTS across the whole recording, and return it as a resolution-independent
    [x1, y1, x2, y2] ratio box — the same shape as streamers.json's hand-configured
    `facecam_box`, just derived automatically from the footage instead of by hand.

    See the block comment above FACECAM_SCAN_SAMPLE_COUNT for the incident this exists to
    prevent and why persistence (rather than confidence, count, or within-clip stability) is
    the signal that separates a camera from a face the game itself is drawing.

    Run ONCE PER SOURCE CHUNK, not per clip — the answer is a property of the recording, and
    sampling within a single clip is exactly the mistake that lets one pack-opening sequence
    look like a stable face. Never raises: an unreadable/unopenable file comes back as a scan
    with samples_read == 0, which callers must treat as "no evidence" rather than "no facecam".
    """
    duration = _video_duration_seconds(video_path)
    if duration <= 0 or sample_count < 1:
        logger.warning(
            "Facecam scan: could not determine a usable duration for %s — skipping scan", video_path,
        )
        return FacecamScan(None, 0.0, 0, 0, 0.0)

    # Skip the very start and end: a chunk's first and last frames are the most likely to be
    # a transition, a black frame, or a partially-written GOP at a recording boundary.
    span_start, span_end = 0.05 * duration, 0.95 * duration
    if sample_count == 1:
        timestamps = [(span_start + span_end) / 2]
    else:
        step = (span_end - span_start) / (sample_count - 1)
        timestamps = [span_start + i * step for i in range(sample_count)]

    per_frame_boxes, per_frame_scores, frame_w, frame_h = _scan_frames_for_faces(video_path, timestamps)
    samples_read = len(per_frame_boxes)
    if samples_read == 0 or frame_w <= 0 or frame_h <= 0:
        logger.warning("Facecam scan: no readable frames in %s — skipping scan", video_path)
        return FacecamScan(None, 0.0, 0, 0, 0.0)

    # Cluster by position across frames. Each cluster counts at most ONE hit per frame (a
    # frame where two detections land in the same cluster is still just one frame's worth of
    # evidence that something is there) so `persistence` stays a true fraction of frames.
    clusters: list[dict] = []
    for boxes, scores in zip(per_frame_boxes, per_frame_scores):
        claimed: set[int] = set()
        for box, score in zip(boxes, scores):
            for index, cluster in enumerate(clusters):
                if _boxes_are_same_position(box, cluster["representative"], frame_w, frame_h):
                    cluster["boxes"].append(box)
                    cluster["scores"].append(score)
                    if index not in claimed:
                        cluster["frames"] += 1
                        claimed.add(index)
                    cluster["representative"] = (
                        int(_median([b[0] for b in cluster["boxes"]])),
                        int(_median([b[1] for b in cluster["boxes"]])),
                        int(_median([b[2] for b in cluster["boxes"]])),
                        int(_median([b[3] for b in cluster["boxes"]])),
                    )
                    break
            else:
                clusters.append({
                    "boxes": [box], "scores": [score], "frames": 1, "representative": box,
                })
                claimed.add(len(clusters) - 1)

    if not clusters:
        logger.info(
            "Facecam scan of %s: no faces at all across %d readable sample(s) — no facecam",
            video_path, samples_read,
        )
        return FacecamScan(None, 0.0, 0, samples_read, 0.0)

    # Most-persistent wins; mean confidence only breaks ties between equally persistent
    # positions. Deliberately NOT score-first: out-scoring the streamer's small corner cam in
    # a given frame is exactly what a big sharp card portrait does.
    best = max(clusters, key=lambda c: (c["frames"], sum(c["scores"]) / len(c["scores"])))
    persistence = best["frames"] / samples_read
    mean_confidence = sum(best["scores"]) / len(best["scores"])

    if persistence < min_persistence:
        logger.info(
            "Facecam scan of %s: best position held only %d/%d sample(s) (%.0f%% < %.0f%% "
            "required) — treating this footage as having NO webcam overlay, so no face zone "
            "gets cropped out of game content",
            video_path, best["frames"], samples_read, persistence * 100, min_persistence * 100,
        )
        return FacecamScan(None, persistence, best["frames"], samples_read, mean_confidence)

    x, y, w, h = best["representative"]
    box_ratio = (x / frame_w, y / frame_h, (x + w) / frame_w, (y + h) / frame_h)
    logger.info(
        "Facecam scan of %s: persistent webcam at ratio box %s — held %d/%d sample(s) "
        "(%.0f%%), mean_confidence=%.2f",
        video_path, tuple(round(v, 4) for v in box_ratio), best["frames"], samples_read,
        persistence * 100, mean_confidence,
    )
    return FacecamScan(box_ratio, persistence, best["frames"], samples_read, mean_confidence)


def get_multi_cam_coordinates(
    boxes: Sequence[tuple[int, int, int, int]], frame_w: int, frame_h: int,
    padding_factor: float = PADDING_FACTOR,
) -> list[tuple[int, int, int, int]]:
    """Pads and clamps each raw face box in `boxes` the same way get_facecam_coordinates() pads
    a single box — one cover-fit-ready crop box per grid slot for build_filter_complex()'s
    multi_cam layout. A box that clamps to near-nothing falls back to its own raw (unpadded)
    box rather than being dropped — dropping a slot here would shrink the vstack tile count out
    from under the layout decision that already committed to len(boxes) slots."""
    result = []
    for b in boxes:
        padded = _pad_and_clamp_box(b, frame_w, frame_h, padding_factor)
        result.append(padded if padded is not None else b)
    return result
