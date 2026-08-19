"""2026-08-19: dynamic layout switching — full_cam (single face filling most of the frame,
e.g. Just-Chatting-style content), split_screen (a small corner webcam box over a much
bigger gameplay area, still exactly one face), or blur_background (zero or multiple faces).
Face count alone can't tell full_cam and split_screen apart since both are "one face" —
face_area_ratio is the second signal that does."""

from pathlib import Path

import pytest

import process


# --- resolve_layout --------------------------------------------------------------------

def test_resolve_layout_manual_choice_bypasses_detection(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("detection should not run for a non-auto layout")
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", boom)
    assert process.resolve_layout(process.LAYOUT_SPLIT_SCREEN, Path("x.mp4"), 1.0) == process.LAYOUT_SPLIT_SCREEN


def test_resolve_layout_no_faces_falls_back_to_blur(monkeypatch):
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (0, None, 1920, 1080))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0) == process.LAYOUT_BLUR_BACKGROUND


def test_resolve_layout_multiple_faces_falls_back_to_blur(monkeypatch):
    monkeypatch.setattr(
        process.vision, "detect_faces_for_layout", lambda *a, **k: (2, (0, 0, 100, 100), 1920, 1080),
    )
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0) == process.LAYOUT_BLUR_BACKGROUND


def test_resolve_layout_one_face_large_ratio_is_full_cam(monkeypatch):
    box = (0, 0, 600, 600)  # 0.36 of a 1000x1000 frame, above the default 0.28 threshold
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0) == process.LAYOUT_FULL_CAM


def test_resolve_layout_one_face_small_ratio_is_split_screen(monkeypatch):
    box = (0, 0, 200, 200)  # 0.04 of a 1000x1000 frame, below the threshold
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0) == process.LAYOUT_SPLIT_SCREEN


def test_resolve_layout_ratio_exactly_at_threshold_is_full_cam(monkeypatch):
    # >= threshold, not >, per resolve_layout's own comparison.
    box = (0, 0, 1000, 280)  # ratio == FULL_CAM_MIN_FACE_AREA_RATIO exactly, on a 1000x1000 frame
    assert process.vision.face_area_ratio(box, 1000, 1000) == process.FULL_CAM_MIN_FACE_AREA_RATIO
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0) == process.LAYOUT_FULL_CAM


def test_resolve_layout_retries_on_ambiguous_first_frame_when_clip_end_given(monkeypatch):
    # First sampled frame looks like 0 faces (a blink, a HUD flicker); the second retry
    # timestamp (clip_start + 1/3 of the duration) finds the real single webcam.
    box = (0, 0, 200, 200)  # small ratio -> split_screen
    calls = []

    def fake_detect(video_path, ts):
        calls.append(ts)
        if len(calls) == 1:
            return 0, None, 1000, 1000
        return 1, box, 1000, 1000

    monkeypatch.setattr(process.vision, "detect_faces_for_layout", fake_detect)
    resolved = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=0.0, clip_end=30.0)

    assert resolved == process.LAYOUT_SPLIT_SCREEN
    assert len(calls) == 2
    assert calls[1] == pytest.approx(10.0)  # 0 + 1/3 * 30


def test_resolve_layout_no_clip_end_does_not_retry(monkeypatch):
    calls = []

    def fake_detect(video_path, ts):
        calls.append(ts)
        return 0, None, 1000, 1000

    monkeypatch.setattr(process.vision, "detect_faces_for_layout", fake_detect)
    process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=5.0)

    assert calls == [5.0]


def test_resolve_layout_still_falls_back_to_blur_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (0, None, 1000, 1000))
    resolved = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=0.0, clip_end=30.0)
    assert resolved == process.LAYOUT_BLUR_BACKGROUND


# --- build_filter_complex: full_cam branch ----------------------------------------------

def test_build_filter_complex_full_cam_crops_source_and_fills_full_canvas(tmp_path):
    ass_path = tmp_path / "subs.ass"
    ass_path.write_text("", encoding="utf-8")
    facecam_box = (10, 20, 300, 400)

    filter_str = process.build_filter_complex(
        process.LAYOUT_FULL_CAM, ass_path, 1080, 1920, facecam_box=facecam_box,
    )

    assert "crop=300:400:10:20" in filter_str
    assert "scale=1080:1920" in filter_str
    assert "[outv]" in filter_str
    # Unlike split_screen, full_cam has no separate gameplay zone/vstack.
    assert "vstack" not in filter_str


def test_build_filter_complex_unknown_layout_still_raises():
    import pytest
    with pytest.raises(ValueError):
        process.build_filter_complex("not_a_real_layout", Path("x.ass"), 1080, 1920)


# --- build_ass_for_clip: white title-box hook --------------------------------------------

def test_build_ass_for_clip_includes_title_box_dialogue_line(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path)
    clip = {"start_time": 0.0, "end_time": 5.0, "title": "Krasser Moment!"}
    transcript = {"segments": []}

    ass_path = process.build_ass_for_clip(clip, transcript, index=1, output_w=1080, output_h=1920)
    content = ass_path.read_text(encoding="utf-8")

    assert ",TitleBox," in content
    assert "Krasser Moment!" in content


def test_build_ass_for_clip_split_screen_anchors_title_to_the_seam(tmp_path, monkeypatch):
    # 2026-08-19: a real render put the title box in the flat-ratio spot, well above the
    # facecam/gameplay seam a reference clip in this style uses — split_screen now centers
    # the title on the seam itself (SPLIT_SCREEN_FACE_RATIO), not the general TITLE_BOX_Y_RATIO.
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path)
    clip = {"start_time": 0.0, "end_time": 5.0, "title": "Krasser Moment!"}
    transcript = {"segments": []}

    ass_path = process.build_ass_for_clip(
        clip, transcript, index=1, output_w=1080, output_h=1920, layout=process.LAYOUT_SPLIT_SCREEN,
    )
    content = ass_path.read_text(encoding="utf-8")

    expected_y = int(1920 * process.SPLIT_SCREEN_FACE_RATIO)
    assert f"\\pos(540,{expected_y})" in content


def test_build_ass_for_clip_full_cam_uses_flat_title_ratio(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path)
    clip = {"start_time": 0.0, "end_time": 5.0, "title": "Krasser Moment!"}
    transcript = {"segments": []}

    ass_path = process.build_ass_for_clip(
        clip, transcript, index=1, output_w=1080, output_h=1920, layout=process.LAYOUT_FULL_CAM,
    )
    content = ass_path.read_text(encoding="utf-8")

    expected_y = int(1920 * process.TITLE_BOX_Y_RATIO)
    assert f"\\pos(540,{expected_y})" in content


def test_build_ass_for_clip_omits_title_box_dialogue_without_a_title(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path)
    clip = {"start_time": 0.0, "end_time": 5.0, "title": ""}
    transcript = {"segments": []}

    ass_path = process.build_ass_for_clip(clip, transcript, index=1, output_w=1080, output_h=1920)
    content = ass_path.read_text(encoding="utf-8")

    assert ",TitleBox," not in content


def test_title_box_and_subtitles_use_different_y_positions():
    # The whole point of raising SUBTITLE_Y_RATIO was to keep it clear of TITLE_BOX_Y_RATIO
    # and, for split_screen, clear of the seam-anchored title position too.
    assert process.TITLE_BOX_Y_RATIO < process.SUBTITLE_Y_RATIO
    assert process.SPLIT_SCREEN_FACE_RATIO < process.SUBTITLE_Y_RATIO
