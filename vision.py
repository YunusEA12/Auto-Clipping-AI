"""Face detection: locate the facecam region in a video frame for dynamic 9:16 cropping."""

import logging
import urllib.request
from pathlib import Path
from typing import Optional

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.core.base_options import BaseOptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "blaze_face_short_range.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
)

# How much to expand the raw face bounding box so head, shoulders and some
# background are visible instead of just the face.
PADDING_FACTOR = 2.5


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


def _fallback_box(frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Top-left corner crop used when no face is detected."""
    w = frame_w
    h = frame_h // 2
    return 0, 0, w, h


def _detect_raw_box(
    video_path: str, timestamp: float
) -> tuple[Optional[tuple[int, int, int, int]], int, int]:
    """Read the frame at `timestamp` and return (raw_box_or_None, frame_w, frame_h)."""
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

    if not result.detections:
        return None, frame_w, frame_h

    box = result.detections[0].bounding_box
    return (box.origin_x, box.origin_y, box.width, box.height), frame_w, frame_h


def has_face(video_path: str, timestamp: float) -> bool:
    """Cheap presence check used to decide between split-screen and blur-background layouts."""
    raw_box, _, _ = _detect_raw_box(video_path, timestamp)
    return raw_box is not None


def get_facecam_coordinates(video_path: str, timestamp: float) -> tuple[int, int, int, int]:
    """Detect a face at `timestamp` seconds into `video_path` and return a padded (x, y, w, h) box in pixels."""
    raw_box, frame_w, frame_h = _detect_raw_box(video_path, timestamp)

    if raw_box is None:
        logger.warning("No face detected at %.2fs in %s, using fallback crop", timestamp, video_path)
        return _fallback_box(frame_w, frame_h)

    x, y, w, h = raw_box

    pad_w = w * (PADDING_FACTOR - 1) / 2
    pad_h = h * (PADDING_FACTOR - 1) / 2

    x1 = max(0, int(x - pad_w))
    y1 = max(0, int(y - pad_h))
    x2 = min(frame_w, int(x + w + pad_w))
    y2 = min(frame_h, int(y + h + pad_h))

    final_x, final_y = x1, y1
    final_w, final_h = x2 - x1, y2 - y1

    logger.info(
        "Face detected at %.2fs: raw=(%d,%d,%d,%d) padded=(%d,%d,%d,%d)",
        timestamp, x, y, w, h, final_x, final_y, final_w, final_h,
    )
    return final_x, final_y, final_w, final_h
