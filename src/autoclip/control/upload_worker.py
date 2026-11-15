"""Publish assessed backlog independently of whether a streamer is currently live."""

import argparse
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from autoclip import runtime
from autoclip.configuration import streamers
from autoclip.control import auto_pilot
from autoclip.core import atomic_io
from autoclip.publishing import upload_ledger, upload_manager
from autoclip.rendering import process

logger = logging.getLogger(__name__)
STATE_PATH = runtime.path("upload_worker_state.json")


def candidates(root: Path | None = None) -> list[tuple[dict, Path, int, str]]:
    output = root or process.OUTPUT_DIR
    queue = []
    for entry in streamers.load_streamers():
        if not (entry.get("auto_upload") and entry.get("publish")):
            continue
        folder = output / process.slugify(entry["name"])
        for clip, video, score in auto_pilot.find_backlog_clips(folder, exclude=set()):
            if (
                type(score) not in (int, float)
                or not math.isfinite(score)
                or not auto_pilot.DEFAULT_PURGE_THRESHOLD <= score <= 10
            ):
                continue
            if clip.get("auto_qc", {}).get("passed") is not True:
                continue  # Historical videos without technical evidence need manual review.
            existing = upload_ledger.get_entry(upload_ledger.compute_content_hash(video), "tiktok")
            if existing and existing.get("status") in ("done", "pending"):
                continue
            queue.append((clip, video, score, entry["name"]))
    queue.sort(key=lambda item: auto_pilot._backlog_sort_key(item[0], item[1]), reverse=True)
    return queue


def run_once(*, publish: bool = False) -> dict:
    from autoclip.control.process_supervisor import read_fleet_target_state

    if read_fleet_target_state() == "paused":
        return {"status": "paused"}
    if publish:
        pacing = upload_manager.tiktok_pacing_status()
        if not pacing["ready"]:
            return {"status": "waiting", **pacing}
    queue = candidates()
    if not publish:
        return {"status": "dry_run", "eligible": len(queue), "publish": False}
    if not queue:
        return {"status": "idle", "eligible": 0}
    clip, video, score, streamer = queue[0]
    auto_pilot._agent_state_path_override = STATE_PATH
    uploaded, failed = auto_pilot.run_deployment_phase([(clip, video, score)], publish=True, streamer_name=streamer)
    return {
        "status": "published" if uploaded else "retry_later",
        "uploaded": uploaded,
        "failed": failed,
        "eligible": len(queue),
        "streamer": streamer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Explicitly enable live publishing")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-interval", type=int, default=60)
    args = parser.parse_args()
    if args.poll_interval < 1:
        parser.error("--poll-interval must be positive")
    runtime.ensure_directories()
    try:
        while True:
            try:
                status = run_once(publish=args.publish)
            except Exception as exc:
                logger.exception("Upload worker deferred after %s", type(exc).__name__)
                status = {"status": "error", "error_type": type(exc).__name__}
            status["checked_at"] = datetime.now(timezone.utc).isoformat()
            atomic_io.atomic_write_json(STATE_PATH, status)
            if args.once:
                print(status)
                return 1 if status["status"] == "error" else 0
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
