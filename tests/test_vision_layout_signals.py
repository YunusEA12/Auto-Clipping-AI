"""2026-08-19: the layout auto-decision (process.resolve_layout) needs to tell "exactly one
face, filling most of the frame" (full_cam) apart from "exactly one face, a small corner
webcam box over a much bigger gameplay area" (split_screen) — face COUNT alone can't do
that, since both are "one face" by count. These functions supply the second signal
(face_area_ratio) and the count itself (previously discarded after result.detections[0])."""

import vision


# --- face_area_ratio (pure function, no MediaPipe involved) -------------------------------

def test_face_area_ratio_full_frame_face():
    # A face box covering the whole frame -> ratio 1.0.
    assert vision.face_area_ratio((0, 0, 1000, 1000), 1000, 1000) == 1.0


def test_face_area_ratio_quarter_frame_face():
    assert vision.face_area_ratio((0, 0, 500, 500), 1000, 1000) == 0.25


def test_face_area_ratio_small_corner_face():
    ratio = vision.face_area_ratio((0, 0, 100, 100), 1000, 1000)
    assert ratio == 0.01


def test_face_area_ratio_zero_frame_area_does_not_crash():
    assert vision.face_area_ratio((0, 0, 100, 100), 0, 0) == 0.0


# --- face_center_x_ratio (pure function, no MediaPipe involved) ---------------------------

def test_face_center_x_ratio_centered_face():
    assert vision.face_center_x_ratio((400, 0, 200, 200), 1000) == 0.5


def test_face_center_x_ratio_left_corner_face():
    ratio = vision.face_center_x_ratio((0, 0, 100, 100), 1000)
    assert ratio == 0.05


def test_face_center_x_ratio_right_corner_face():
    ratio = vision.face_center_x_ratio((900, 0, 100, 100), 1000)
    assert ratio == 0.95


def test_face_center_x_ratio_zero_frame_width_does_not_crash():
    assert vision.face_center_x_ratio((0, 0, 100, 100), 0) == 0.5


# --- detect_faces_for_layout ---------------------------------------------------------------

def test_detect_faces_for_layout_no_faces(monkeypatch):
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([], [], 1920, 1080))
    count, box, w, h = vision.detect_faces_for_layout("video.mp4", 1.0)
    assert count == 0
    assert box is None
    assert (w, h) == (1920, 1080)


def test_detect_faces_for_layout_one_face(monkeypatch):
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: ([(10, 20, 300, 300)], [0.9], 1920, 1080),
    )
    count, box, w, h = vision.detect_faces_for_layout("video.mp4", 1.0)
    assert count == 1
    assert box == (10, 20, 300, 300)


def test_detect_faces_for_layout_multiple_confident_faces_returns_highest_scoring_box(monkeypatch):
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: (
            [(10, 20, 300, 300), (500, 20, 300, 300)], [0.7, 0.95], 1920, 1080,
        ),
    )
    count, box, w, h = vision.detect_faces_for_layout("video.mp4", 1.0)
    assert count == 2
    assert box == (500, 20, 300, 300)  # the higher-scoring detection, not just the first


def test_detect_faces_for_layout_low_confidence_second_detection_not_counted(monkeypatch):
    # A gameplay HUD element (character portrait, kill-cam face) clearing MediaPipe's own
    # baseline 0.5 floor but not FACE_COUNT_MIN_CONFIDENCE shouldn't be counted as a second
    # person and push a genuine single-webcam clip into the blur_background fallback.
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: (
            [(10, 20, 300, 300), (500, 20, 50, 50)], [0.9, 0.55], 1920, 1080,
        ),
    )
    count, box, w, h = vision.detect_faces_for_layout("video.mp4", 1.0)
    assert count == 1
    assert box == (10, 20, 300, 300)


# --- has_face built on the same shared detection call --------------------------------------

def test_has_face_true_with_detections(monkeypatch):
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([(0, 0, 100, 100)], [0.9], 1920, 1080))
    assert vision.has_face("video.mp4", 1.0) is True


def test_has_face_false_without_detections(monkeypatch):
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([], [], 1920, 1080))
    assert vision.has_face("video.mp4", 1.0) is False


# --- get_facecam_coordinates padding_factor (full_cam needs more headroom than
# split_screen's default when the same padded box gets stretched to fill the whole tall
# canvas instead of just a short top-third zone) --------------------------------------------

def test_get_facecam_coordinates_larger_padding_factor_yields_larger_box(monkeypatch):
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: ([(500, 500, 200, 200)], [0.9], 1920, 1080),
    )

    default_box = vision.get_facecam_coordinates("video.mp4", 1.0)
    wide_box = vision.get_facecam_coordinates("video.mp4", 1.0, padding_factor=vision.PADDING_FACTOR * 2)

    assert wide_box[2] > default_box[2]  # width
    assert wide_box[3] > default_box[3]  # height


def test_get_facecam_coordinates_falls_back_without_a_face(monkeypatch):
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([], [], 1920, 1080))
    box = vision.get_facecam_coordinates("video.mp4", 1.0)
    # _fallback_box always returns a quarter-frame corner box.
    assert box == (960, 0, 960, 540)


def test_get_facecam_coordinates_uses_highest_scoring_box(monkeypatch):
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: (
            [(10, 20, 300, 300), (500, 20, 50, 50)], [0.6, 0.95], 1920, 1080,
        ),
    )
    box = vision.get_facecam_coordinates("video.mp4", 1.0)
    # Crops toward the higher-confidence detection, not just boxes[0].
    assert box[:2] != (10, 20)


# --- padding_factor_from_margin -------------------------------------------------------------

def test_padding_factor_from_margin_zero_margin_is_identity():
    assert vision.padding_factor_from_margin(0.0) == 1.0


def test_padding_factor_from_margin_quarter_margin():
    # 25% extra margin on each side -> padding_factor 1.5 (pad = size * (1.5-1)/2 = 0.25*size).
    assert vision.padding_factor_from_margin(0.25) == 1.5


# --- get_facecam_coordinates temporal smoothing (clip_end given) ---------------------------

def test_get_facecam_coordinates_smooths_across_multiple_samples(monkeypatch):
    # A single outlier sample (far off from the rest, simulating a blink/motion-blur/HUD-
    # glitch frame) must not drag the whole clip's crop box toward it -- the median of several
    # samples should land on the well-agreed-upon position instead.
    per_ts_boxes = {
        0.0: (100, 100, 200, 200),
        2.5: (105, 98, 200, 200),
        5.0: (900, 900, 20, 20),  # outlier
        7.5: (102, 101, 200, 200),
        10.0: (98, 103, 200, 200),
    }

    def fake_detect_faces(video_path, timestamp):
        box = per_ts_boxes[timestamp]
        return [box], [0.9], 1920, 1080

    monkeypatch.setattr(vision, "_detect_faces", fake_detect_faces)

    box = vision.get_facecam_coordinates("video.mp4", 0.0, clip_end=10.0, sample_count=5)
    # Padded box should still be centered near the agreed-upon ~(100,100,200,200) cluster, not
    # anywhere near the (900,900) outlier.
    assert 900 not in box


def test_get_facecam_coordinates_without_clip_end_uses_single_frame(monkeypatch):
    calls = []

    def fake_detect_faces(video_path, timestamp):
        calls.append(timestamp)
        return [(10, 20, 300, 300)], [0.9], 1920, 1080

    monkeypatch.setattr(vision, "_detect_faces", fake_detect_faces)
    vision.get_facecam_coordinates("video.mp4", 1.0)
    assert calls == [1.0]


def test_get_facecam_coordinates_with_clip_end_samples_multiple_frames(monkeypatch):
    calls = []

    def fake_detect_faces(video_path, timestamp):
        calls.append(timestamp)
        return [(10, 20, 300, 300)], [0.9], 1920, 1080

    monkeypatch.setattr(vision, "_detect_faces", fake_detect_faces)
    vision.get_facecam_coordinates("video.mp4", 0.0, clip_end=8.0, sample_count=4)
    assert calls == [0.0, 8.0 / 3, 16.0 / 3, 8.0]


# --- get_facecam_coordinates streamer override (low-confidence fallback) -------------------

def test_get_facecam_coordinates_uses_override_below_confidence_threshold(monkeypatch):
    monkeypatch.setattr(
        vision, "_detect_faces",
        # 0.3 is below MediaPipe's own ~0.5 baseline floor, so this score can't occur with the
        # real detector -- it only exists here to exercise the low-confidence override branch.
        lambda video_path, timestamp: ([(10, 20, 300, 300)], [0.3], 1920, 1080),  # below 0.50
    )
    box = vision.get_facecam_coordinates(
        "video.mp4", 1.0, override_box_ratio=[0.5, 0.0, 1.0, 0.5],
    )
    assert box == (960, 0, 960, 540)


def test_get_facecam_coordinates_ignores_override_at_or_above_confidence_threshold(monkeypatch):
    # 0.50 is DYNAMIC_DETECTION_MIN_CONFIDENCE itself (inclusive boundary) -- a streamer who
    # moved/resized their cam still has a genuine, if marginal, detection at this score, and
    # that real detection must win over the stale static override.
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: ([(10, 20, 300, 300)], [0.5], 1920, 1080),
    )
    box = vision.get_facecam_coordinates(
        "video.mp4", 1.0, override_box_ratio=[0.5, 0.0, 1.0, 0.5],
    )
    # Dynamic detection wins -- override box (960, 0, 960, 540) was not used.
    assert box != (960, 0, 960, 540)


def test_get_facecam_coordinates_uses_override_when_no_face_detected(monkeypatch):
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([], [], 1920, 1080))
    box = vision.get_facecam_coordinates(
        "video.mp4", 1.0, override_box_ratio=[0.5, 0.0, 1.0, 0.5],
    )
    assert box == (960, 0, 960, 540)


def test_get_facecam_coordinates_falls_back_to_generic_box_without_override(monkeypatch):
    # No override configured -- unchanged behavior, generic quarter-frame corner fallback.
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([], [], 1920, 1080))
    box = vision.get_facecam_coordinates("video.mp4", 1.0)
    assert box == (960, 0, 960, 540)


# --- is_multi_cam_scene (pure function) -----------------------------------------------------
# 2026-08-24 incident: a Discord call / podcast / collab grid needs individual per-speaker
# slots, not the single-primary-face full_cam/split_screen decision — see resolve_layout()'s
# own docstring in process.py for the live symptom (a forced 2-slot split onto a multi-tile
# grid producing large black gaps between zones).

def test_is_multi_cam_scene_true_for_similarly_sized_faces_in_range():
    boxes = [(0, 0, 100, 100), (200, 0, 100, 100), (400, 0, 100, 100)]
    scores = [0.9, 0.85, 0.88]
    assert vision.is_multi_cam_scene(boxes, scores) is True


def test_is_multi_cam_scene_false_below_min_faces():
    boxes = [(0, 0, 100, 100)]
    scores = [0.9]
    assert vision.is_multi_cam_scene(boxes, scores) is False


def test_is_multi_cam_scene_false_above_max_faces():
    boxes = [(i * 100, 0, 90, 90) for i in range(6)]
    scores = [0.9] * 6
    assert vision.is_multi_cam_scene(boxes, scores) is False


def test_is_multi_cam_scene_false_when_low_confidence_faces_excluded_drop_below_min():
    boxes = [(0, 0, 100, 100), (200, 0, 100, 100)]
    scores = [0.9, 0.5]  # second face doesn't clear FACE_COUNT_MIN_CONFIDENCE
    assert vision.is_multi_cam_scene(boxes, scores) is False


def test_is_multi_cam_scene_false_when_one_face_dominates_area():
    # One real streamer face plus a small false-positive (a HUD icon, a face within reacted
    # content) -- lopsided area ratio, not a grid of similarly-sized tiles.
    boxes = [(0, 0, 600, 600), (700, 0, 50, 50)]
    scores = [0.9, 0.9]
    assert vision.is_multi_cam_scene(boxes, scores) is False


def test_is_multi_cam_scene_true_at_the_area_ratio_spread_boundary():
    boxes = [(0, 0, 100, 100), (200, 0, 173, 173)]  # areas 10000 vs 29929 -> ratio ~2.99
    scores = [0.9, 0.9]
    assert vision.is_multi_cam_scene(boxes, scores) is True


# --- detect_multi_cam_faces -----------------------------------------------------------------

def test_detect_multi_cam_faces_returns_boxes_in_reading_order(monkeypatch):
    # Bottom-left, top-right, top-left -- must come back sorted (y, x): top-left, top-right,
    # bottom-left.
    boxes = [(500, 400, 100, 100), (500, 0, 100, 100), (0, 0, 100, 100)]
    scores = [0.9, 0.9, 0.9]
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: (boxes, scores, 1920, 1080))
    result = vision.detect_multi_cam_faces("video.mp4", 1.0)
    assert result is not None
    ordered_boxes, w, h = result
    assert ordered_boxes == [(0, 0, 100, 100), (500, 0, 100, 100), (500, 400, 100, 100)]
    assert (w, h) == (1920, 1080)


def test_detect_multi_cam_faces_none_when_not_a_multi_cam_scene(monkeypatch):
    monkeypatch.setattr(
        vision, "_detect_faces", lambda video_path, timestamp: ([(0, 0, 100, 100)], [0.9], 1920, 1080),
    )
    assert vision.detect_multi_cam_faces("video.mp4", 1.0) is None


def test_detect_multi_cam_faces_none_on_read_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("could not read frame")
    monkeypatch.setattr(vision, "_detect_faces", boom)
    assert vision.detect_multi_cam_faces("video.mp4", 1.0) is None


# --- get_multi_cam_coordinates ---------------------------------------------------------------

def test_get_multi_cam_coordinates_pads_each_box():
    boxes = [(100, 100, 100, 100), (500, 100, 100, 100)]
    result = vision.get_multi_cam_coordinates(boxes, 1920, 1080, padding_factor=2.0)
    assert len(result) == 2
    for (x, y, w, h) in result:
        assert w > 100 and h > 100  # padded larger than the raw 100x100 box


def test_get_multi_cam_coordinates_falls_back_to_raw_box_when_padding_degenerates():
    # A box hard against the frame edge that would clamp to nothing under heavy padding falls
    # back to its own raw box rather than being dropped (shrinking the vstack tile count out
    # from under a layout decision that already committed to len(boxes) slots).
    boxes = [(0, 0, 1, 1)]
    result = vision.get_multi_cam_coordinates(boxes, 1920, 1080, padding_factor=2.0)
    assert result == [(0, 0, 1, 1)]


# --- looks_like_busy_multi_face_scene ---------------------------------------------------------

def test_looks_like_busy_multi_face_scene_true_with_two_raw_detections(monkeypatch):
    # Below FACE_COUNT_MIN_CONFIDENCE but still 2 raw detections -- a 9-tile collab grid's
    # small tiles routinely score this low without ever reading as "zero faces" in reality.
    monkeypatch.setattr(
        vision, "_detect_faces",
        lambda video_path, timestamp: ([(0, 0, 50, 50), (100, 0, 50, 50)], [0.5, 0.52], 1920, 1080),
    )
    assert vision.looks_like_busy_multi_face_scene("video.mp4", 1.0) is True


def test_looks_like_busy_multi_face_scene_false_with_one_or_zero_detections(monkeypatch):
    monkeypatch.setattr(vision, "_detect_faces", lambda video_path, timestamp: ([], [], 1920, 1080))
    assert vision.looks_like_busy_multi_face_scene("video.mp4", 1.0) is False


def test_looks_like_busy_multi_face_scene_false_on_read_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("could not read frame")
    monkeypatch.setattr(vision, "_detect_faces", boom)
    assert vision.looks_like_busy_multi_face_scene("video.mp4", 1.0) is False


# --- box_is_mostly_black ----------------------------------------------------------------------

class _FakeCapture:
    def __init__(self, frame):
        self._frame = frame
        self._opened = frame is not None

    def isOpened(self):
        return self._opened

    def set(self, *a, **k):
        pass

    def read(self):
        if self._frame is None:
            return False, None
        return True, self._frame

    def release(self):
        pass


def test_box_is_mostly_black_true_for_a_black_region(monkeypatch):
    import numpy as np
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    monkeypatch.setattr(vision.cv2, "VideoCapture", lambda path: _FakeCapture(frame))
    assert vision.box_is_mostly_black("video.mp4", 1.0, (0, 0, 500, 500)) is True


def test_box_is_mostly_black_false_for_a_bright_region(monkeypatch):
    import numpy as np
    frame = np.full((1080, 1920, 3), 200, dtype=np.uint8)
    monkeypatch.setattr(vision.cv2, "VideoCapture", lambda path: _FakeCapture(frame))
    assert vision.box_is_mostly_black("video.mp4", 1.0, (0, 0, 500, 500)) is False


def test_box_is_mostly_black_false_below_the_fraction_threshold(monkeypatch):
    import numpy as np
    # 25% black -- below the 0.3 default threshold for "mostly" black.
    frame = np.full((1080, 1920, 3), 200, dtype=np.uint8)
    frame[:, :125] = 0  # left quarter of the 500-wide sample box is black -> 25% black
    monkeypatch.setattr(vision.cv2, "VideoCapture", lambda path: _FakeCapture(frame))
    assert vision.box_is_mostly_black("video.mp4", 1.0, (0, 0, 500, 500)) is False


def test_box_is_mostly_black_false_when_capture_fails_to_open(monkeypatch):
    monkeypatch.setattr(vision.cv2, "VideoCapture", lambda path: _FakeCapture(None))
    assert vision.box_is_mostly_black("video.mp4", 1.0, (0, 0, 500, 500)) is False


def test_box_is_mostly_black_false_on_degenerate_zero_size_region(monkeypatch):
    import numpy as np
    frame = np.full((1080, 1920, 3), 200, dtype=np.uint8)
    monkeypatch.setattr(vision.cv2, "VideoCapture", lambda path: _FakeCapture(frame))
    assert vision.box_is_mostly_black("video.mp4", 1.0, (0, 0, 0, 0)) is False
