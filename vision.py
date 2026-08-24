"""Face detection: locate the facecam region in a video frame for dynamic 9:16 cropping."""

import logging
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

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
    """
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

    x, y, w, h = raw_box

    pad_w = w * (padding_factor - 1) / 2
    pad_h = h * (padding_factor - 1) / 2

    x1 = max(0, int(x - pad_w))
    y1 = max(0, int(y - pad_h))
    x2 = min(frame_w, int(x + w + pad_w))
    y2 = min(frame_h, int(y + h + pad_h))

    final_x, final_y = x1, y1
    final_w, final_h = x2 - x1, y2 - y1

    if final_w < MIN_BOX_DIMENSION or final_h < MIN_BOX_DIMENSION:
        logger.warning(
            "Degenerate face box at %.2fs (%dx%d after clamping to frame edges), using fallback crop",
            timestamp, final_w, final_h,
        )
        return _fallback_box(frame_w, frame_h)

    logger.info(
        "Face detected (%s): raw=(%d,%d,%d,%d) padded=(%d,%d,%d,%d)",
        where, x, y, w, h, final_x, final_y, final_w, final_h,
    )
    return final_x, final_y, final_w, final_h
