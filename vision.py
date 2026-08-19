"""Face detection: locate the facecam region in a video frame for dynamic 9:16 cropping."""

import logging
import urllib.request
from pathlib import Path
from typing import Optional

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
) -> tuple[list[tuple[int, int, int, int]], int, int]:
    """Read the frame at `timestamp` and return (all_raw_boxes, frame_w, frame_h) — every
    face MediaPipe found, not just the first. Previously this only ever kept
    result.detections[0] and discarded the count entirely, so there was no way to tell "one
    face" from "several faces" anywhere in this module (found in review, 2026-08-19, needed
    for the full-cam/split-screen/blur-background layout decision below)."""
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
    return boxes, frame_w, frame_h


def has_face(video_path: str, timestamp: float) -> bool:
    """Cheap presence check."""
    boxes, _, _ = _detect_faces(video_path, timestamp)
    return len(boxes) > 0


def detect_faces_for_layout(video_path: str, timestamp: float) -> tuple[int, Optional[tuple[int, int, int, int]], int, int]:
    """One detection pass covering everything process.resolve_layout() needs to pick between
    full_cam / split_screen / blur_background: (face_count, first_raw_box_or_None, frame_w,
    frame_h). A single call instead of running detection twice (once for a presence/count
    check, once for the box) — the raw (unpadded) box lets the caller measure how much of
    the frame the face actually covers via face_area_ratio() below."""
    boxes, frame_w, frame_h = _detect_faces(video_path, timestamp)
    first_box = boxes[0] if boxes else None
    return len(boxes), first_box, frame_w, frame_h


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


def get_facecam_coordinates(
    video_path: str, timestamp: float, padding_factor: float = PADDING_FACTOR
) -> tuple[int, int, int, int]:
    """Detect a face at `timestamp` seconds into `video_path` and return a padded (x, y, w, h) box in pixels.

    Detection runs once, at the clip's start_time, and that single box is used as a
    static crop for the whole clip's render — so there is no per-frame jitter or
    flicker within a clip by construction (no frame-by-frame re-detection to disagree
    with itself).

    `padding_factor` defaults to PADDING_FACTOR (tuned for split_screen's top-third zone);
    process.py passes a larger value for full_cam, where the same box gets stretched to fill
    the ENTIRE tall 9:16 canvas instead of a short-wide strip — cover-fitting a padded box
    tuned for a short zone into a much taller one crops far tighter than intended.
    """
    boxes, frame_w, frame_h = _detect_faces(video_path, timestamp)
    raw_box = boxes[0] if boxes else None

    if raw_box is None:
        logger.warning("No face detected at %.2fs in %s, using fallback crop", timestamp, video_path)
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
        "Face detected at %.2fs: raw=(%d,%d,%d,%d) padded=(%d,%d,%d,%d)",
        timestamp, x, y, w, h, final_x, final_y, final_w, final_h,
    )
    return final_x, final_y, final_w, final_h
