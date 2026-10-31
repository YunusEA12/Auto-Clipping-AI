"""A stable face in the reacted interview must not become a streamer facecam."""
import pytest

import vision


def scan(box, *, samples=16, persistence=1.):
    return vision.FacecamScan(box, persistence, samples, samples, .95)


def test_persistent_centered_interview_face_is_not_a_webcam():
    result = vision.assess_facecam_source(scan((.40, .15, .60, .40)))
    assert result["passed"] is False
    assert result["reason"] == "facecam_source_region_not_peripheral"


def test_large_interview_face_is_not_a_webcam_even_near_edge():
    result = vision.assess_facecam_source(scan((.02, .02, .50, .55)))
    assert result["passed"] is False
    assert result["reason"] == "facecam_source_region_too_large"


@pytest.mark.parametrize("box", [(.80, .05, .94, .25), (.04, .65, .20, .90)])
def test_small_persistent_corner_region_is_eligible(box):
    result = vision.assess_facecam_source(scan(box))
    assert result["passed"] is True
    assert result["box_ratio"] == box
    assert result["limitation"] == "identity_not_proven"


@pytest.mark.parametrize("evidence", [None, scan(None, samples=0)])
def test_unreadable_scan_fails_closed(evidence):
    assert not vision.assess_facecam_source(evidence)["passed"]


def test_only_a_few_readable_frames_are_not_source_evidence():
    result = vision.assess_facecam_source(scan((.8, .05, .94, .25), samples=4))
    assert result["reason"] == "insufficient_facecam_source_samples"


@pytest.mark.parametrize("persistence", [.2, .5, float("nan")])
def test_nonpersistent_source_fails(persistence):
    assert not vision.assess_facecam_source(scan((.8, .05, .94, .25), persistence=persistence))["passed"]


def test_operator_region_is_returned_unchanged_even_when_scan_finds_another_face():
    configured = (.76, .01, .99, .40)
    result = vision.assess_facecam_source(scan((.40, .15, .60, .40)), configured)
    assert result["passed"] is True
    assert result["source"] == "configured_region"
    assert result["box_ratio"] == configured


@pytest.mark.parametrize("box", [[], [0, 0, 1], [0, 0, float("nan"), 1],
                                 [0, 0, 2, 1], [True, 0, 1, 1], [.5, 0, .1, 1]])
def test_invalid_operator_regions_are_not_replaced_with_dynamic_faces(box):
    result = vision.assess_facecam_source(scan((.8, .05, .94, .25)), box)
    assert result["passed"] is False
    assert result["reason"] == "invalid_configured_facecam_region"
    assert result["box_ratio"] is None


def test_configured_roi_cannot_be_overridden_by_confident_interview_face(monkeypatch):
    monkeypatch.setattr(vision, "_frame_size", lambda _: (1920, 1080))
    def unexpected(*args, **kwargs):
        raise AssertionError("A configured camera region must not search the content for another face")
    monkeypatch.setattr(vision, "_detect_faces", unexpected)
    monkeypatch.setattr(vision, "_sample_smoothed_box", unexpected)
    result = vision.get_facecam_coordinates(
        "source.ts", 5, clip_end=30, override_box_ratio=(.75, 0, 1., .4),
        verified_box_ratio=(.4, .15, .6, .4), configured_roi_only=True,
    )
    assert result == (1440, 0, 480, 432)


def test_configured_roi_fails_closed_when_dimensions_unreadable(monkeypatch):
    monkeypatch.setattr(vision, "_frame_size", lambda _: (0, 0))
    with pytest.raises(RuntimeError, match="dimensions"):
        vision.get_facecam_coordinates("source.ts", 0, override_box_ratio=(.75, 0, 1, .4), configured_roi_only=True)


@pytest.mark.parametrize("configured", [None, (0, 0, 2, 1)])
def test_configured_roi_mode_requires_valid_manual_region(configured):
    with pytest.raises(RuntimeError, match="valid configured"):
        vision.get_facecam_coordinates("source.ts", 0, override_box_ratio=configured, configured_roi_only=True)
