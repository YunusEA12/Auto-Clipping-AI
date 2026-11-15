"""Pure rendering settings shared with the dashboard; imports no models."""

VIDEO_FORMATS = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}

DEFAULT_FORMAT = "9:16"

LAYOUT_SPLIT_SCREEN = "split_screen"

LAYOUT_BLUR_BACKGROUND = "blur_background"

LAYOUT_FULL_CAM = "full_cam"

LAYOUT_CENTER_CROP = "center_crop"

LAYOUT_MULTI_CAM = "multi_cam"

LAYOUT_AUTO = "auto"

SELECTABLE_LAYOUTS = (
    LAYOUT_SPLIT_SCREEN,
    LAYOUT_BLUR_BACKGROUND,
    LAYOUT_FULL_CAM,
    LAYOUT_CENTER_CROP,
    LAYOUT_MULTI_CAM,
    LAYOUT_AUTO,
)

HIGHLIGHT_COLORS = {
    "Gelb (Hormozi)": "00FFFF",
    "Neon Grün": "00FF66",
    "Weiß": "FFFFFF",
}

DEFAULT_HIGHLIGHT_COLOR = HIGHLIGHT_COLORS["Gelb (Hormozi)"]

LAYOUT_RETRY_FRACTIONS = (1 / 3, 2 / 3)
