"""2026-08-19: dynamic layout switching — full_cam (a face filling most of the frame, e.g.
Just-Chatting-style content), split_screen (a small corner webcam box over a much bigger
gameplay area), or blur_background (zero confident faces at all — nothing to crop toward).
Face count alone can't tell full_cam and split_screen apart since both can have the SAME
face_count — face_area_ratio is the second signal that does.

2026-08-21: the full_cam/split_screen decision now runs off the primary (highest-scoring)
face whenever face_count >= 1, not just when it's exactly 1 — see resolve_layout()'s own
docstring for why (a real Fortnite clip's confident corner facecam plus a confident-enough
kill-cam/character portrait used to land on face_count=2 and get discarded into
blur_background instead of split_screen)."""

from pathlib import Path

import pytest

import process


# --- resolve_layout --------------------------------------------------------------------

def test_resolve_layout_manual_choice_bypasses_detection(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("detection should not run for a non-auto layout")
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", boom)
    resolved, detection_ts = process.resolve_layout(process.LAYOUT_SPLIT_SCREEN, Path("x.mp4"), 1.0)
    assert resolved == process.LAYOUT_SPLIT_SCREEN
    assert detection_ts == 1.0


def test_resolve_layout_no_faces_falls_back_to_center_crop(monkeypatch):
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (0, None, 1920, 1080))
    resolved, detection_ts = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)
    assert resolved == process.LAYOUT_CENTER_CROP
    assert detection_ts == 1.0


def test_resolve_layout_multiple_confident_faces_still_uses_the_primary_one(monkeypatch):
    # 2026-08-21: changed from falling back to blur_background on any face_count != 1 — a
    # second confident detection in this pipeline's actual content (solo Twitch gaming clips)
    # is far more often a game-HUD/kill-cam false positive than a real second person, so a
    # small off-center primary box now still resolves to split_screen instead of being
    # discarded (found live: a real Fortnite clip's corner facecam plus a confident kill-cam
    # portrait used to do exactly this).
    box = (0, 0, 100, 100)  # small + off-center on a 1920x1080 frame -> split_screen
    monkeypatch.setattr(
        process.vision, "detect_faces_for_layout", lambda *a, **k: (2, box, 1920, 1080),
    )
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_SPLIT_SCREEN


def test_resolve_layout_multiple_confident_faces_with_large_primary_is_full_cam(monkeypatch):
    # Same face_count=2 scenario, other branch: a large/centered primary box still resolves
    # to full_cam even with a second confident detection alongside it.
    box = (0, 0, 600, 600)  # 0.36 of a 1000x1000 frame, above the default 0.28 threshold
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (2, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_FULL_CAM


def test_resolve_layout_one_face_large_ratio_is_full_cam(monkeypatch):
    box = (0, 0, 600, 600)  # 0.36 of a 1000x1000 frame, above the default 0.28 threshold
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_FULL_CAM


def test_resolve_layout_one_face_small_ratio_is_split_screen(monkeypatch):
    box = (0, 0, 200, 200)  # 0.04 of a 1000x1000 frame, below the threshold
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_SPLIT_SCREEN


def test_resolve_layout_ratio_exactly_at_threshold_is_full_cam(monkeypatch):
    # >= threshold, not >, per resolve_layout's own comparison.
    box = (0, 0, 1000, 280)  # ratio == FULL_CAM_MIN_FACE_AREA_RATIO exactly, on a 1000x1000 frame
    assert process.vision.face_area_ratio(box, 1000, 1000) == process.FULL_CAM_MIN_FACE_AREA_RATIO
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_FULL_CAM


def test_resolve_layout_small_but_centered_face_is_full_cam(monkeypatch):
    # A wide/distant Just-Chatting shot: face reads small (well below the area threshold) but
    # sits horizontally centered, not pushed to a corner -- must NOT be mistaken for a
    # gameplay-corner facecam (found live, 2026-08-19).
    box = (450, 400, 100, 100)  # 0.01 of a 1000x1000 frame, centered (center_x_ratio == 0.5)
    assert process.vision.face_area_ratio(box, 1000, 1000) < process.FULL_CAM_MIN_FACE_AREA_RATIO
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_FULL_CAM


def test_resolve_layout_small_and_off_center_face_is_split_screen(monkeypatch):
    # A genuine corner gameplay facecam: small AND pushed to the edge -- still split_screen.
    box = (0, 0, 100, 100)  # 0.01 of a 1000x1000 frame, center_x_ratio == 0.05 (corner)
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (1, box, 1000, 1000))
    assert process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), 1.0)[0] == process.LAYOUT_SPLIT_SCREEN


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
    resolved, detection_ts = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=0.0, clip_end=30.0)

    assert resolved == process.LAYOUT_SPLIT_SCREEN
    assert len(calls) == 2
    assert calls[1] == pytest.approx(10.0)  # 0 + 1/3 * 30
    # 2026-08-22 fix: detection_ts is the retry timestamp the face was actually found at, not
    # clip_start — the caller (process_clips_iter) reuses it for the real facecam crop instead
    # of re-detecting at clip_start, which was faceless here.
    assert detection_ts == pytest.approx(10.0)


# --- unreadable-frame retry timestamps (2026-08-20: found live -- a retry timestamp
# (clip_start + fraction * duration) can land just past the chunk .ts file's actual readable
# length, e.g. the recording ending a little short of the clip's own end_time. cv2 then fails
# the frame read outright (vision._detect_faces raises RuntimeError) instead of returning 0
# faces -- previously this propagated straight up and crashed the ENTIRE cycle, wasting every
# other clip in it, instead of just falling back for this one clip like a genuine 0-face
# reading already does) ------------------------------------------------------------------

def test_resolve_layout_recovers_from_unreadable_frame_by_trying_next_timestamp(monkeypatch):
    box = (0, 0, 200, 200)
    calls = []

    def fake_detect(video_path, ts):
        calls.append(ts)
        if len(calls) == 1:
            raise RuntimeError(f"Could not read frame at {ts:.2f}s from {video_path}")
        return 1, box, 1000, 1000

    monkeypatch.setattr(process.vision, "detect_faces_for_layout", fake_detect)
    resolved, _ = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=0.0, clip_end=30.0)

    assert resolved == process.LAYOUT_SPLIT_SCREEN
    assert len(calls) == 2


def test_resolve_layout_falls_back_to_center_crop_when_every_timestamp_is_unreadable(monkeypatch):
    def always_unreadable(video_path, ts):
        raise RuntimeError(f"Could not read frame at {ts:.2f}s from {video_path}")

    monkeypatch.setattr(process.vision, "detect_faces_for_layout", always_unreadable)
    resolved, detection_ts = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=0.0, clip_end=30.0)

    assert resolved == process.LAYOUT_CENTER_CROP
    assert detection_ts == 0.0


def test_resolve_layout_no_clip_end_does_not_retry(monkeypatch):
    calls = []

    def fake_detect(video_path, ts):
        calls.append(ts)
        return 0, None, 1000, 1000

    monkeypatch.setattr(process.vision, "detect_faces_for_layout", fake_detect)
    process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=5.0)

    assert calls == [5.0]


def test_resolve_layout_still_falls_back_to_center_crop_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(process.vision, "detect_faces_for_layout", lambda *a, **k: (0, None, 1000, 1000))
    resolved, detection_ts = process.resolve_layout(process.LAYOUT_AUTO, Path("x.mp4"), clip_start=0.0, clip_end=30.0)
    assert resolved == process.LAYOUT_CENTER_CROP
    assert detection_ts == 0.0


# --- process_clips_iter: facecam crop must use resolve_layout's own detection_ts, not
# clip_start (2026-08-22 fix) -- previously process_clips_iter() re-detected the face at
# clip["start_time"] unconditionally, independently of whichever timestamp resolve_layout()
# actually found a face at (clip_start itself, or a LAYOUT_RETRY_FRACTIONS retry). If the face
# was only found on a retry, this second, independent detection at clip_start could come back
# empty and silently fall back to vision._fallback_box()'s static corner box instead of the
# real, just-confirmed face -- live in the default LAYOUT_AUTO path. ------------------------

def test_process_clips_iter_crops_at_resolve_layouts_detection_ts_not_clip_start(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(process, "find_source_video", lambda explicit: tmp_path / "source.mp4")
    monkeypatch.setattr(process, "get_video_dimensions", lambda video_path: (1920, 1080))
    monkeypatch.setattr(process, "_has_audio_stream", lambda video_path: False)
    monkeypatch.setattr(process, "pick_background_track", lambda: None)
    monkeypatch.setattr(process, "build_ass_for_clip", lambda *a, **k: tmp_path / "subs.ass")
    monkeypatch.setattr(process, "_write_clip_metadata_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(process, "render_clip", lambda *a, **k: tmp_path / "clip_1.mp4")
    monkeypatch.setattr(process.atomic_io, "atomic_write_json", lambda *a, **k: None)

    # resolve_layout says split_screen, found on the retry at 3.33s -- clip_start (0.0) was
    # faceless. The crop call below must use 3.33, not 0.0.
    monkeypatch.setattr(process, "resolve_layout", lambda *a, **k: (process.LAYOUT_SPLIT_SCREEN, 3.33))

    captured_timestamps = []

    def fake_get_facecam_coordinates(video_path, timestamp, **kwargs):
        captured_timestamps.append(timestamp)
        return (0, 0, 100, 100)

    monkeypatch.setattr(process.vision, "get_facecam_coordinates", fake_get_facecam_coordinates)

    clips_data = {"clips": [{"start_time": 0.0, "end_time": 10.0, "title": "Clip"}]}
    monkeypatch.setattr(
        process, "load_json",
        lambda path: dict(clips_data) if "clips" in path.name else {"segments": []},
    )

    list(process.process_clips_iter(
        source_video=tmp_path / "source.mp4", layout=process.LAYOUT_AUTO, output_dir=tmp_path / "out",
    ))

    assert captured_timestamps == [3.33]


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
