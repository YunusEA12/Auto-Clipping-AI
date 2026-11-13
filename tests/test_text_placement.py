"""Title/subtitle placement, checked on the real build_ass_for_clip() output."""
import re

import auto_qc
import process
import text_placement

W, H = 1080, 1920
WORDS = ["UNGLAUBLICH", "SPANNENDER", "AUGENBLICK", "GEWESEN"]
TRANSCRIPT = {"segments": [{"start": 0, "end": 4, "words": [
    {"start": i, "end": i + .9, "text": w} for i, w in enumerate(WORDS)]}]}
CLIP = {"start_time": 0, "end_time": 4, "title": "Dieser Moment ist einfach"}
# 1600x900 source, face ~200px wide padded 2.5x -> typical split_screen crop
SPLIT_BOX = (550, 150, 500, 360)
FULL_BOX = (300, 0, 900, 900)


def build(tmp_path, monkeypatch, layout, **kw):
    monkeypatch.setattr(process, "TEMP_DIR", tmp_path)
    return process.build_ass_for_clip(CLIP, TRANSCRIPT, 1, output_w=W, output_h=H, layout=layout, **kw)


def positions(path):
    text = path.read_text(encoding="utf-8")
    sub = [tuple(map(int, m)) for m in re.findall(r"Default,,0,\d+,0,,\{\\an5\\pos\((\d+),(\d+)\)", text)]
    title = [tuple(map(int, m)) for m in re.findall(r"TitleBox,,0,0,0,,\{\\an5\\pos\((\d+),(\d+)\)", text)]
    return sub[0], title[0]


def test_split_screen_title_sits_below_facecam_zone(tmp_path, monkeypatch):
    path = build(tmp_path, monkeypatch, process.LAYOUT_SPLIT_SCREEN, facecam_box=SPLIT_BOX)
    _, (_, title_y) = positions(path)
    half = text_placement.title_half_height(CLIP["title"], W)
    assert title_y - half >= H * process.SPLIT_SCREEN_FACE_RATIO


def test_split_screen_text_avoids_face_rect_and_passes_qc(tmp_path, monkeypatch):
    path = build(tmp_path, monkeypatch, process.LAYOUT_SPLIT_SCREEN, facecam_box=SPLIT_BOX)
    rects = text_placement.face_rects("split_screen", W, H, SPLIT_BOX, face_zone_ratio=process.SPLIT_SCREEN_FACE_RATIO)
    assert rects
    assert auto_qc.check_subtitles(path, rects)["passed"]


def test_full_cam_moves_text_off_face(tmp_path, monkeypatch):
    # Face fills the middle of the canvas; both texts must end up outside that rectangle.
    path = build(tmp_path, monkeypatch, process.LAYOUT_FULL_CAM, facecam_box=FULL_BOX)
    rect = text_placement.face_rects("full_cam", W, H, FULL_BOX)[0]
    (sx, sy), (_, ty) = positions(path)
    assert not (rect[1] < sy < rect[3])
    assert not (rect[1] < ty < rect[3])
    assert auto_qc.check_subtitles(path, [rect])["passed"]


def test_subtitle_moves_when_face_would_cover_it(tmp_path, monkeypatch):
    # A face rect placed exactly on the default subtitle position forces a move.
    default_y = int(H * process.SUBTITLE_Y_RATIO)
    rects = [(0, default_y - 150, W, default_y + 150)]
    placed = text_placement.compute_text_placement(
        "multi_cam", W, H, "Titel", process.SUBTITLE_Y_RATIO, process.TITLE_BOX_Y_RATIO, rects)
    assert abs(placed["subtitle_y"] - default_y) >= 150
    half = text_placement.subtitle_half_height()
    assert not (rects[0][1] < placed["subtitle_y"] + half and placed["subtitle_y"] - half < rects[0][3])


def test_subtitles_stay_left_of_tiktok_button_column(tmp_path, monkeypatch):
    path = build(tmp_path, monkeypatch, process.LAYOUT_CENTER_CROP)
    text = path.read_text(encoding="utf-8")
    col = text_placement.subtitle_column(W)
    assert col["x"] + (W - col["margin_l"] - col["margin_r"]) / 2 <= W * text_placement.UI_RIGHT_X_RATIO
    assert f"Default,,0,{col['margin_r']},0,,{{\\an5\\pos({col['x']}," in text
    assert auto_qc.check_subtitles(path)["passed"]


def test_qc_rejects_subtitle_over_face(tmp_path, monkeypatch):
    path = build(tmp_path, monkeypatch, process.LAYOUT_CENTER_CROP)
    (_, sy), _ = positions(path)
    assert not auto_qc.check_subtitles(path, [(0, sy - 50, W, sy + 50)])["passed"]


def test_qc_rejects_subtitle_in_button_column(tmp_path, monkeypatch):
    path = build(tmp_path, monkeypatch, process.LAYOUT_CENTER_CROP)
    text = path.read_text(encoding="utf-8")
    col = text_placement.subtitle_column(W)
    # Legacy centring with the old margins runs into the button column on a full-width line.
    legacy = text.replace(f"\\pos({col['x']},", "\\pos(540,").replace(f",0,{col['margin_r']},0,,", ",0,0,0,,")
    path.write_text(legacy, encoding="utf-8")
    assert not auto_qc.check_subtitles(path)["passed"]


def test_no_face_info_keeps_legacy_ratios(tmp_path, monkeypatch):
    path = build(tmp_path, monkeypatch, process.LAYOUT_SPLIT_SCREEN)
    (_, sy), (tx, ty) = positions(path)
    assert sy == int(H * process.SUBTITLE_Y_RATIO)
    assert (tx, ty) == (540, int(H * process.SPLIT_SCREEN_TITLE_Y_RATIO))
