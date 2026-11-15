"""Draw the account's avatar offline, with no image API and no new dependency.

Why this exists: the Gemini image models are at `limit: 0` on this project's free tier (image
generation simply isn't included), and the OpenAI SDK was deliberately removed from this
project in August. Rather than reintroduce a paid dependency for one PNG, the avatar is
drawn from flat shapes with Pillow, which the pipeline already uses.

Everything is drawn at SUPERSAMPLE times the target size and downscaled at the end, which is
what keeps the curves smooth — Pillow's draw primitives have no anti-aliasing of their own.

Usage:
    python scripts/draw_avatar.py
    python scripts/draw_avatar.py --size 1024 --out profile_assets/avatar.png
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from autoclip.core import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "profile_assets" / "avatar.png"

SUPERSAMPLE = 4

# Flat palette, high contrast so the avatar still reads at TikTok's smallest circle size.
BACKGROUND = (255, 214, 102)
POTATO_FILL = (196, 134, 74)
POTATO_SHADE = (173, 113, 58)
OUTLINE = (58, 38, 24)
HEADPHONE_FILL = (38, 166, 154)
HEADPHONE_DARK = (23, 128, 118)
WHITE = (255, 255, 255)
BLUSH = (226, 146, 110)


def _blob(draw: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, fill, outline, width: int) -> None:
    """A potato silhouette: narrower at the top, heavier and wider at the bottom.

    Drawn as a filled polygon rather than overlapping ellipses — overlapping ellipses leave
    their seams visible once a darker outline is stroked over only one of them, which read
    as smudges on the first version of this avatar.
    """
    import math

    points = []
    for step in range(240):
        angle = (step / 240) * 2 * math.pi
        # Asymmetric radius: wider below the waist, slightly pinched above it, plus a gentle
        # lobe on one side so the shape is not mirror-symmetric.
        vertical = math.sin(angle)
        widen = 1.0 + 0.13 * vertical + 0.05 * math.sin(angle * 2)
        heighten = 1.0 + 0.04 * math.cos(angle)
        points.append((cx + rx * widen * math.cos(angle), cy + ry * heighten * vertical))

    draw.polygon(points, fill=fill)
    draw.line(points + [points[0]], fill=outline, width=width, joint="curve")


def draw_avatar(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    image = Image.new("RGB", (s, s), BACKGROUND)
    draw = ImageDraw.Draw(image)

    stroke = max(2, int(s * 0.012))
    cx, cy = s / 2, s / 2 + s * 0.02

    # A ring of lighter background keeps the subject off the circular crop edge.
    draw.ellipse([s * 0.06, s * 0.06, s * 0.94, s * 0.94], fill=(255, 231, 153))

    body_rx, body_ry = s * 0.27, s * 0.31

    # Headphone band first, so the body overlaps it and the band reads as going behind.
    band_rx, band_ry = s * 0.305, s * 0.345
    draw.arc(
        [cx - band_rx, cy - band_ry, cx + band_rx, cy + band_ry],
        start=200,
        end=340,
        fill=HEADPHONE_DARK,
        width=int(stroke * 3.2),
    )

    _blob(draw, cx, cy, body_rx, body_ry, POTATO_FILL, OUTLINE, stroke)

    # Potato eyes (the sprouting kind) as small darker dashes, purely decorative texture.
    # Kept high and to the sides: anything near the lower centre reads as a stain on the
    # chin or as a stray mark beside the mouth.
    for dx, dy in ((-0.62, -0.50), (0.60, -0.56), (-0.74, 0.08)):
        ex, ey = cx + body_rx * dx, cy + body_ry * dy
        r = s * 0.011
        draw.ellipse([ex - r, ey - r, ex + r, ey + r], fill=POTATO_SHADE)

    # Face.
    eye_dx, eye_y = body_rx * 0.36, cy - body_ry * 0.18
    eye_r = s * 0.052
    for sign in (-1, 1):
        ex = cx + sign * eye_dx
        draw.ellipse([ex - eye_r, eye_y - eye_r, ex + eye_r, eye_y + eye_r], fill=WHITE, outline=OUTLINE, width=stroke)
        pr = eye_r * 0.52
        draw.ellipse([ex - pr, eye_y - pr * 0.3, ex + pr, eye_y + pr * 1.7], fill=OUTLINE)
        gr = pr * 0.34
        draw.ellipse([ex - pr * 0.1, eye_y + pr * 0.1, ex - pr * 0.1 + gr * 2, eye_y + pr * 0.1 + gr * 2], fill=WHITE)

    # Smile: an arc, with its ends rounded off by two dots so it doesn't look cut.
    mouth_w, mouth_y = body_rx * 0.40, cy + body_ry * 0.22
    draw.arc(
        [cx - mouth_w, mouth_y - body_ry * 0.16, cx + mouth_w, mouth_y + body_ry * 0.22],
        start=20,
        end=160,
        fill=OUTLINE,
        width=stroke,
    )
    for sign in (-1, 1):
        mr = stroke * 0.5
        mxp = cx + sign * mouth_w * 0.94
        myp = mouth_y + body_ry * 0.005
        draw.ellipse([mxp - mr, myp - mr, mxp + mr, myp + mr], fill=OUTLINE)

    for sign in (-1, 1):
        bx = cx + sign * body_rx * 0.70
        br = s * 0.030
        draw.ellipse([bx - br, mouth_y - br * 0.6, bx + br, mouth_y + br * 0.6], fill=BLUSH)

    # Ear cups last: they sit on top of everything.
    cup_rx, cup_ry = s * 0.072, s * 0.095
    for sign in (-1, 1):
        ux = cx + sign * (body_rx + s * 0.015)
        uy = cy - body_ry * 0.06
        draw.rounded_rectangle(
            [ux - cup_rx, uy - cup_ry, ux + cup_rx, uy + cup_ry],
            radius=cup_rx * 0.75,
            fill=HEADPHONE_FILL,
            outline=OUTLINE,
            width=stroke,
        )
        draw.rounded_rectangle(
            [ux - cup_rx * 0.46, uy - cup_ry * 0.58, ux + cup_rx * 0.46, uy + cup_ry * 0.58],
            radius=cup_rx * 0.4,
            fill=HEADPHONE_DARK,
        )

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw the account avatar offline with Pillow.")
    parser.add_argument("--size", type=int, default=1024, help="Output edge length in pixels (default: 1024)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Where to write the PNG")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image = draw_avatar(args.size)
    image.save(out_path, format="PNG", optimize=True)
    logger.info(
        "✅ Avatar gezeichnet: %s (%dx%d, %d KB)", out_path, args.size, args.size, out_path.stat().st_size // 1024
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
