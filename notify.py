"""Notification step: post a formatted Discord message when a clip is rendered/uploaded."""

import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATUS_EMOJI = {
    "uploaded": "✅",
    "failed": "⚠️",
    "rendered": "🎬",
    "skipped": "⏭️",
}


def send_notification(title: str, energy: int, filepath: Path, upload_status: str) -> bool:
    """Post a formatted Discord webhook notification. Never raises — a missing/broken
    webhook must never take down the rendering/upload pipeline that calls this. Returns
    True if the message was actually sent, False if skipped or failed.
    """
    load_dotenv()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.info("DISCORD_WEBHOOK_URL not set, skipping notification for '%s'", title)
        return False

    energy = max(0, min(10, energy))
    energy_bar = "🔥" * energy + "▫️" * (10 - energy)
    status_emoji = STATUS_EMOJI.get(upload_status, "ℹ️")

    content = (
        f"**🎬 Neuer Clip gerendert!**\n"
        f"**Titel:** {title}\n"
        f"**Energie-Level:** {energy_bar} ({energy}/10)\n"
        f"**Datei:** `{Path(filepath).name}`\n"
        f"**Status:** {status_emoji} {upload_status}"
    )

    try:
        response = requests.post(webhook_url, json={"content": content}, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to send Discord notification for '%s': %s", title, e)
        return False

    logger.info("Sent Discord notification for '%s' (status=%s)", title, upload_status)
    return True
