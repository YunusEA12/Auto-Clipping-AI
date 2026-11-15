"""Generate the new TikTok account's avatar and profile copy.

The account is deliberately a character ("Kartoffel schaut Twitch") rather than a channel
named after a streamer: a profile carrying a streamer's name or face is impersonation under
TikTok's rules, and it also guarantees the "unoriginal content" classification that blocks
monetisation. See obsidian_vault/02_wiki/TikTok-Accountwechsel.md.

The avatar is generated rather than taken from the web on purpose — a reused image would be
someone else's copyright on the one asset that represents the account.

Outputs land in profile_assets/ (gitignored, it holds generated binaries):
    avatar.png        square, ready to upload as the profile picture
    profile.json      display name, username suggestions and bio, as structured data
    profile.txt       the same copy, formatted for reading/pasting

Usage:
    python scripts/generate_profile_assets.py
    python scripts/generate_profile_assets.py --model gemini-3-pro-image --force
"""

import argparse
import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

import atomic_io
import logging_setup

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

OUTPUT_DIR = REPO_ROOT / "profile_assets"

# Ordered like llm_utils.DEFAULT_MODEL_POOL: the free tier's request quota is per model per
# day, so one exhausted model must not take the whole run down with it.
IMAGE_MODEL_POOL = [
    "gemini-3.1-flash-image",
    "gemini-2.5-flash-image",
    "gemini-3-pro-image",
]

AVATAR_PROMPT = (
    "A friendly cartoon potato wearing large over-ear gaming headphones, front-facing, "
    "smiling, big expressive eyes. Flat vector illustration, bold clean outlines, warm "
    "golden-brown potato, teal headphones. Simple flat background in a single soft colour, "
    "no text, no letters, no logos, no watermark. Centred composition with generous margin, "
    "designed to stay readable when cropped to a small circle. Square image."
)

# TikTok renders the avatar as a small circle, so anything near the edge is lost.
AVATAR_SIZE = 1024

PROFILE_COPY = {
    "display_name": "Kartoffel schaut Twitch",
    "username_suggestions": [
        "kartoffelschauttwitch",
        "kartoffel.schaut.twitch",
        "diekartoffelschaut",
        "kartoffel.clips",
    ],
    "bio": (
        "Ich bin eine Kartoffel. Ich schaue Twitch. \U0001F954\U0001F3A7\n"
        "Die besten Momente landen hier."
    ),
    # Kept deliberately short: TikTok's bio limit is 80 characters, and the app silently
    # truncates rather than warning. Counted below so a longer rewrite fails loudly.
    "bio_limit": 80,
    "notes": [
        "Privates Konto, kein Business-Konto: Business-Konten haben eine eingeschraenkte "
        "Sound-Bibliothek und werden fuer Clip-Content schlechter ausgespielt.",
        "Kein Streamer-Name und kein Streamer-Gesicht im Profil (Impersonation).",
    ],
}


def _generate_avatar(client, model: str) -> bytes:
    """Returns PNG bytes for the avatar, or raises."""
    response = client.models.generate_content(model=model, contents=AVATAR_PROMPT)
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data
    raise RuntimeError(f"{model} returned no image data")


def _to_square_png(raw: bytes, size: int) -> bytes:
    """Normalises whatever the model returned into a square RGB PNG.

    TikTok rejects some odd-shaped or alpha-channel avatars outright rather than cropping
    them, so this is not cosmetic.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(raw))
    if image.mode != "RGB":
        # Flatten any transparency onto white rather than letting it turn black.
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1] if image.mode in ("RGBA", "LA") else None)
        image = background

    # Centre-crop to a square before resizing, so nothing is squashed.
    width, height = image.size
    if width != height:
        edge = min(width, height)
        left = (width - edge) // 2
        top = (height - edge) // 2
        image = image.crop((left, top, left + edge, top + edge))

    image = image.resize((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the new account's avatar and profile copy.")
    parser.add_argument("--model", default=None, help="Force one image model instead of trying the pool")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing avatar.png")
    parser.add_argument("--copy-only", action="store_true", help="Only write the profile copy, skip the image")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    bio = PROFILE_COPY["bio"]
    # A silently truncated bio is worse than a failed run: the account would go live with a
    # cut-off sentence and nobody would notice.
    if len(bio) > PROFILE_COPY["bio_limit"]:
        logger.error(
            "Bio is %d characters, over TikTok's %d-character limit — shorten PROFILE_COPY['bio'].",
            len(bio), PROFILE_COPY["bio_limit"],
        )
        return 1

    atomic_io.atomic_write_json(OUTPUT_DIR / "profile.json", PROFILE_COPY, indent=2, ensure_ascii=False)
    readable = "\n".join([
        f"Anzeigename : {PROFILE_COPY['display_name']}",
        f"Benutzername: {' | '.join(PROFILE_COPY['username_suggestions'])}",
        "",
        f"Bio ({len(bio)}/{PROFILE_COPY['bio_limit']} Zeichen):",
        bio,
        "",
        "Hinweise:",
        *(f"- {n}" for n in PROFILE_COPY["notes"]),
    ])
    atomic_io.atomic_write_text(OUTPUT_DIR / "profile.txt", readable + "\n")
    logger.info("Profiltexte geschrieben nach %s", OUTPUT_DIR)

    if args.copy_only:
        return 0

    avatar_path = OUTPUT_DIR / "avatar.png"
    if avatar_path.exists() and not args.force:
        logger.info("%s existiert schon — mit --force neu erzeugen.", avatar_path)
        return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY fehlt in der Umgebung/.env — Avatar kann nicht erzeugt werden.")
        return 1

    from google import genai

    client = genai.Client(api_key=api_key)
    models = [args.model] if args.model else IMAGE_MODEL_POOL

    last_error = None
    for model in models:
        try:
            logger.info("Erzeuge Avatar mit %s ...", model)
            raw = _generate_avatar(client, model)
        except Exception as e:
            logger.warning("%s fehlgeschlagen: %s", model, str(e)[:200])
            last_error = e
            continue

        png = _to_square_png(raw, AVATAR_SIZE)
        avatar_path.write_bytes(png)
        logger.info("✅ Avatar gespeichert: %s (%d KB, %dx%d)", avatar_path, len(png) // 1024, AVATAR_SIZE, AVATAR_SIZE)
        return 0

    logger.error("Kein Bildmodell lieferte ein Bild. Letzter Fehler: %s", last_error)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
