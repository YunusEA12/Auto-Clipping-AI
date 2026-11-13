"""Where the title box and word-by-word subtitles go on the 9:16 canvas.

Pure geometry, no I/O. Keeps text (a) off the face(s) that the chosen layout puts on the
canvas and (b) out of TikTok's own UI zones: the button column on the right edge and the
caption/description area at the bottom. process.build_ass_for_clip() calls compute_text_placement();
auto_qc.check_subtitles() re-checks the written .ass against the same face rectangles.

Rect = (x0, y0, x1, y1) in output-canvas pixels.
"""

import math
from typing import Optional, Sequence

Rect = tuple

# TikTok UI zones as fractions of the canvas (eyeballed against the app, same caveat as the
# *_Y_RATIO constants in process.py).
UI_RIGHT_X_RATIO = 0.90     # like/comment/share column starts right of this
UI_BOTTOM_Y_RATIO = 0.82    # caption/description area starts below this
UI_TOP_Y_RATIO = 0.10       # search/back chrome (titles may use down to 5%, see TITLE_TOP_Y_RATIO)
TITLE_TOP_Y_RATIO = 0.05

SUBTITLE_FONT = 80
SUBTITLE_OUTLINE = 5
SUBTITLE_MAX_LINES = 2      # WORDS_PER_BLOCK=4 uppercase words can wrap to two lines
TITLE_FONT = 58
TITLE_BORDER = 24
SIDE_MARGIN = 50            # matches the Style lines' MarginL/MarginR

FACE_SAFETY_SCALE = 1.25    # hair / chin / shoulders beyond the detected face box
SEARCH_STEP = 10


def subtitle_half_height(lines: int = SUBTITLE_MAX_LINES) -> float:
    return lines * SUBTITLE_FONT / 2 + SUBTITLE_OUTLINE


def title_lines(title: str, output_w: int) -> int:
    em = TITLE_FONT * 0.63
    available = output_w - 2 * SIDE_MARGIN - 2 * TITLE_BORDER
    per_line = max(1, int(available // em))
    return max(1, math.ceil(len((title or "").strip()) / per_line))


def title_half_height(title: str, output_w: int) -> float:
    return title_lines(title, output_w) * TITLE_FONT / 2 + TITLE_BORDER


def subtitle_column(output_w: int) -> dict:
    """Text column that stays clear of the right-hand button column: x anchor + ASS margins."""
    right_edge = int(output_w * UI_RIGHT_X_RATIO)
    margin_r = output_w - right_edge
    return {"x": (SIDE_MARGIN + right_edge) // 2, "margin_l": SIDE_MARGIN, "margin_r": margin_r}


def _face_rect_in_zone(box: Sequence[int], zone: Rect, padding_factor: float) -> Optional[tuple]:
    """Where the face sits after "cover" scaling a padded crop box into `zone`.

    vision pads the face box by `padding_factor` around its centre, so the face is the middle
    1/padding_factor of the crop; cover-scaling then crops the overhang symmetrically."""
    _, _, box_w, box_h = box
    if box_w <= 0 or box_h <= 0:
        return None
    zx0, zy0, zx1, zy1 = zone
    zw, zh = zx1 - zx0, zy1 - zy0
    scale = max(zw / box_w, zh / box_h)
    face_w = min(box_w * scale / padding_factor * FACE_SAFETY_SCALE, zw)
    face_h = min(box_h * scale / padding_factor * FACE_SAFETY_SCALE, zh)
    cx, cy = (zx0 + zx1) / 2, (zy0 + zy1) / 2
    return (cx - face_w / 2, cy - face_h / 2, cx + face_w / 2, cy + face_h / 2)


def face_rects(
    layout: str, output_w: int, output_h: int,
    facecam_box: Optional[Sequence[int]] = None,
    multi_cam_boxes: Optional[Sequence[Sequence[int]]] = None,
    padding_factor: Optional[float] = None,
    face_zone_ratio: float = 1 / 3,
) -> list:
    """Canvas rectangles that hold a face for this layout; [] when no face is promised
    (center_crop / blur_background, or no box known)."""
    import vision  # lazy: keeps this module importable without cv2 in light tests
    if layout == "split_screen" and facecam_box is not None:
        zone = (0, 0, output_w, int(output_h * face_zone_ratio))
        rect = _face_rect_in_zone(facecam_box, zone, padding_factor or vision.PADDING_FACTOR)
        return [rect] if rect else []
    if layout == "full_cam" and facecam_box is not None:
        zone = (0, 0, output_w, output_h)
        rect = _face_rect_in_zone(facecam_box, zone, padding_factor or 4.5)
        return [rect] if rect else []
    if layout == "multi_cam" and multi_cam_boxes:
        n = len(multi_cam_boxes)
        slot_h = output_h / n
        rects = []
        for i, box in enumerate(multi_cam_boxes):
            rect = _face_rect_in_zone(box, (0, i * slot_h, output_w, (i + 1) * slot_h), padding_factor or vision.PADDING_FACTOR)
            if rect:
                rects.append(rect)
        return rects
    return []


def _overlaps(y: float, half: float, x_half: float, x_center: float, rects: Sequence[Rect]) -> bool:
    for x0, y0, x1, y1 in rects:
        if y + half > y0 and y - half < y1 and x_center + x_half > x0 and x_center - x_half < x1:
            return True
    return False


def _place(preferred: float, half: float, lo: float, hi: float, x_half: float, x_center: float, avoid: Sequence[Rect]) -> float:
    """Centre y closest to `preferred` within [lo, hi] that overlaps none of `avoid`.
    If nothing is free, the allowed position with least overlap is not guessed: return the
    clamped preference so behaviour matches the legacy fixed ratios."""
    preferred = min(max(preferred, lo), hi)
    if not _overlaps(preferred, half, x_half, x_center, avoid):
        return preferred
    offset = SEARCH_STEP
    while offset <= (hi - lo):
        for y in (preferred - offset, preferred + offset):
            if lo <= y <= hi and not _overlaps(y, half, x_half, x_center, avoid):
                return y
        offset += SEARCH_STEP
    return preferred


def compute_text_placement(
    layout: str, output_w: int, output_h: int, title: str,
    subtitle_y_ratio: float, title_y_ratio: float,
    rects: Sequence[Rect] = (), face_zone_ratio: float = 1 / 3,
) -> dict:
    """{"subtitle_y", "title_y"} (centre y in px) honouring face `rects` and TikTok UI zones.

    With no rects the legacy ratios are returned unchanged."""
    sub_y = int(output_h * subtitle_y_ratio)
    title_y = int(output_h * title_y_ratio)
    if not rects:
        return {"subtitle_y": sub_y, "title_y": title_y}

    t_half = title_half_height(title, output_w)
    s_half = subtitle_half_height()
    col = subtitle_column(output_w)
    s_x_half = (output_w - col["margin_l"] - col["margin_r"]) / 2
    t_x_half = (output_w - 2 * SIDE_MARGIN) / 2

    pref_title = title_y
    if layout == "split_screen":
        # The whole top third is the facecam: put the hook just under the seam, on the gameplay.
        pref_title = int(output_h * face_zone_ratio) + 24 + t_half
    title_lo = output_h * TITLE_TOP_Y_RATIO + t_half
    title_hi = output_h * UI_BOTTOM_Y_RATIO - t_half
    title_y = int(_place(pref_title, t_half, title_lo, title_hi, t_x_half, output_w / 2, rects))

    # Subtitles must also avoid the title box, which is on screen for the whole clip.
    title_rect = (SIDE_MARGIN, title_y - t_half, output_w - SIDE_MARGIN, title_y + t_half)
    sub_lo = output_h * UI_TOP_Y_RATIO + s_half
    sub_hi = output_h * UI_BOTTOM_Y_RATIO - s_half
    sub_y = int(_place(sub_y, s_half, sub_lo, sub_hi, s_x_half, col["x"], [*rects, title_rect]))
    return {"subtitle_y": sub_y, "title_y": title_y}
