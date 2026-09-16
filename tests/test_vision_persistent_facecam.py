"""2026-08-25 incident: two EA FC pack-opening clips shipped with the split_screen face zone
showing a zoomed crop of a player CARD (its portrait art) instead of the streamer — the same
content as the gameplay zone below it, just zoomed.

Every existing guard passed it legitimately: a card portrait IS a photograph of a real human
face, so face_count is 1 (not a grid, not busy, not crowded), the box is bright gold (not
black), and during a walkout animation the card holds still long enough that the within-clip
median is stable. The missing discriminator is that a webcam is fixed furniture for the WHOLE
broadcast while game content is transient — which is what locate_persistent_facecam() measures.
"""

import pytest

import vision


# --- _boxes_are_same_position (the clustering predicate) --------------------------------------

def test_identical_boxes_are_the_same_position():
    box = (100, 100, 200, 200)
    assert vision._boxes_are_same_position(box, box, 1920, 1080) is True


def test_slightly_shifted_box_is_still_the_same_position():
    # A streamer leaning/turning inside their fixed webcam rectangle: the detected FACE moves,
    # the CAMERA does not. This must still cluster, or a real webcam would never reach
    # FACECAM_MIN_PERSISTENCE.
    a = (100, 100, 200, 200)
    b = (120, 110, 205, 195)
    assert vision._boxes_are_same_position(a, b, 1920, 1080) is True


def test_box_across_the_frame_is_a_different_position():
    corner_cam = (1600, 60, 200, 200)
    centre_screen_card = (800, 500, 220, 220)
    assert vision._boxes_are_same_position(corner_cam, centre_screen_card, 1920, 1080) is False


def test_same_centre_but_wildly_different_size_is_not_the_same_face():
    # A large centre-screen card portrait must not absorb a small webcam that happens to
    # overlap it.
    small = (900, 500, 60, 60)
    huge = (700, 300, 500, 500)
    assert vision._boxes_are_same_position(small, huge, 1920, 1080) is False


def test_degenerate_inputs_do_not_raise():
    assert vision._boxes_are_same_position((0, 0, 0, 0), (0, 0, 10, 10), 1920, 1080) is False
    assert vision._boxes_are_same_position((0, 0, 10, 10), (0, 0, 10, 10), 0, 0) is False


# --- locate_persistent_facecam ----------------------------------------------------------------

def _fake_scan(monkeypatch, per_frame_boxes, per_frame_scores, frame_w=1920, frame_h=1080,
               duration=600.0):
    monkeypatch.setattr(vision, "_video_duration_seconds", lambda path: duration)
    monkeypatch.setattr(
        vision, "_scan_frames_for_faces",
        lambda path, timestamps: (per_frame_boxes, per_frame_scores, frame_w, frame_h),
    )


def test_persistent_corner_webcam_is_found(monkeypatch):
    # Same corner position in every sampled frame, wobbling slightly — a real webcam.
    boxes = [[(1600 + i % 3, 60 + i % 4, 200, 200)] for i in range(16)]
    scores = [[0.8] for _ in range(16)]
    _fake_scan(monkeypatch, boxes, scores)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.box_ratio is not None
    assert result.persistence == 1.0
    assert result.samples_read == 16
    x1, y1, x2, y2 = result.box_ratio
    assert 0.8 < x1 < 0.9   # right-hand corner, as a fraction of frame width
    assert x2 > x1 and y2 > y1


def test_transient_game_content_is_rejected(monkeypatch):
    # The reported defect: a card portrait appears for a couple of samples out of 16, at a
    # different place each time (different cards, different animation stages). Nothing persists,
    # so this footage correctly reports NO webcam.
    boxes = [[] for _ in range(16)]
    boxes[3] = [(800, 400, 260, 260)]
    boxes[4] = [(500, 200, 300, 300)]
    scores = [[] for _ in range(16)]
    scores[3] = [0.92]   # a card portrait scores HIGH -- confidence alone never catches this
    scores[4] = [0.95]
    _fake_scan(monkeypatch, boxes, scores)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.box_ratio is None
    assert result.scan_succeeded is True   # it ran fine -- "no webcam" is a real answer here
    assert result.persistence < vision.FACECAM_MIN_PERSISTENCE


def test_persistent_webcam_wins_over_a_higher_scoring_transient_face(monkeypatch):
    # The weaker version of the same bug: the streamer's small corner cam is on screen the
    # whole time, but in the frames where a card is shown the card out-scores it. Ranking by
    # persistence (not confidence) must still pick the corner cam.
    webcam = (1600, 60, 160, 160)
    boxes = []
    scores = []
    for i in range(16):
        if i in (5, 6):
            boxes.append([webcam, (820, 430, 300, 300)])
            scores.append([0.71, 0.97])
        else:
            boxes.append([webcam])
            scores.append([0.71])
    _fake_scan(monkeypatch, boxes, scores)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.box_ratio is not None
    x1, _, x2, _ = result.box_ratio
    assert x1 > 0.8, "should have locked onto the corner webcam, not the centre-screen card"
    assert x2 <= 1.0


def test_no_faces_at_all_reports_no_facecam_but_a_successful_scan(monkeypatch):
    _fake_scan(monkeypatch, [[] for _ in range(16)], [[] for _ in range(16)])

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.box_ratio is None
    assert result.scan_succeeded is True
    assert result.samples_with_face == 0


def test_unreadable_video_reports_a_failed_scan_not_a_missing_facecam(monkeypatch):
    # Critical distinction: callers veto split_screen on "no facecam", so an unreadable file
    # must NOT masquerade as evidence that there is no camera.
    _fake_scan(monkeypatch, [], [], frame_w=0, frame_h=0)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.box_ratio is None
    assert result.scan_succeeded is False


def test_unknown_duration_reports_a_failed_scan(monkeypatch):
    monkeypatch.setattr(vision, "_video_duration_seconds", lambda path: 0.0)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.scan_succeeded is False


def test_a_frame_with_two_detections_in_one_cluster_counts_once(monkeypatch):
    # Persistence must stay a true fraction of FRAMES: a frame where the detector fires twice
    # inside the same webcam rectangle is still one frame's worth of evidence, not two.
    boxes = [[(1600, 60, 200, 200), (1605, 65, 198, 202)] for _ in range(4)]
    scores = [[0.8, 0.79] for _ in range(4)]
    _fake_scan(monkeypatch, boxes, scores)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.samples_with_face == 4
    assert result.persistence == 1.0


def test_min_persistence_is_respected(monkeypatch):
    # Present in 8/16 samples == 0.5 persistence.
    boxes = [[(1600, 60, 200, 200)] if i % 2 == 0 else [] for i in range(16)]
    scores = [[0.8] if i % 2 == 0 else [] for i in range(16)]
    _fake_scan(monkeypatch, boxes, scores)

    assert vision.locate_persistent_facecam("chunk.ts", min_persistence=0.4).box_ratio is not None
    assert vision.locate_persistent_facecam("chunk.ts", min_persistence=0.6).box_ratio is None


def test_box_ratio_is_resolution_independent(monkeypatch):
    # The ratio box is fed back through _ratio_box_to_pixels against whatever resolution a
    # given chunk actually has, so it must be fractions of the frame, not pixels.
    boxes = [[(960, 540, 192, 108)] for _ in range(8)]
    scores = [[0.8] for _ in range(8)]
    _fake_scan(monkeypatch, boxes, scores, frame_w=1920, frame_h=1080)

    result = vision.locate_persistent_facecam("chunk.ts")

    assert result.box_ratio == pytest.approx((0.5, 0.5, 0.6, 0.6))


# --- get_facecam_coordinates: verified box outranks per-clip detection ------------------------

def test_verified_box_is_used_instead_of_per_clip_detection(monkeypatch):
    # The whole point: in the defective case the frame's highest-scoring face IS the game
    # content, so a verified whole-chunk position must not defer to it.
    monkeypatch.setattr(vision, "_frame_size", lambda path: (1000, 1000))

    def should_not_run(*a, **k):
        raise AssertionError("per-clip detection must not run when a verified box is given")
    monkeypatch.setattr(vision, "_detect_faces", should_not_run)
    monkeypatch.setattr(vision, "_sample_smoothed_box", should_not_run)

    box = vision.get_facecam_coordinates(
        "chunk.ts", 1.0, verified_box_ratio=(0.8, 0.05, 0.95, 0.2),
    )

    x, y, w, h = box
    assert w > 0 and h > 0
    assert x >= 0 and y >= 0


def test_verified_box_gets_padding_applied(monkeypatch):
    # Unlike a streamers.json override (already an explicit crop box), the verified box is a
    # raw FACE box and needs head/shoulder headroom added.
    monkeypatch.setattr(vision, "_frame_size", lambda path: (1000, 1000))
    ratio = (0.4, 0.4, 0.5, 0.5)  # a 100x100 raw box in the middle of the frame

    tight = vision.get_facecam_coordinates("c.ts", 1.0, padding_factor=1.0, verified_box_ratio=ratio)
    padded = vision.get_facecam_coordinates("c.ts", 1.0, padding_factor=3.0, verified_box_ratio=ratio)

    assert padded[2] > tight[2] and padded[3] > tight[3]


def test_unreadable_frame_size_falls_through_to_detection(monkeypatch):
    monkeypatch.setattr(vision, "_frame_size", lambda path: (0, 0))
    monkeypatch.setattr(vision, "_detect_faces", lambda path, ts: ([(10, 10, 300, 300)], [0.9], 1000, 1000))

    box = vision.get_facecam_coordinates("c.ts", 1.0, verified_box_ratio=(0.1, 0.1, 0.2, 0.2))

    assert box is not None and box[2] > 0
