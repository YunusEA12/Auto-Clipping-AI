"""Move the pipeline onto a freshly created TikTok account.

The reach collapse on the previous account is attributed to volume (151 uploads in a
single day, see obsidian_vault/02_wiki/TikTok-Accountwechsel.md). Carrying that account's
learned state over to the new one would carry the same bad patterns with it, so this
script archives the account-specific learning files and resets the pacing clock, while
deliberately KEEPING the upload ledger — it is what stops already-posted clips from being
re-uploaded to the new account as duplicate content.

It does not touch cookies.json: run get_cookies.py for that, after logging into the new
account in your normal browser. This script refuses to run while the fleet is up, because
a live auto_pilot would write the archived files straight back.

Usage:
    python scripts/switch_tiktok_account.py --dry-run     # show what would happen
    python scripts/switch_tiktok_account.py               # do it
    python scripts/switch_tiktok_account.py --clear-output  # also clear rendered backlog
"""

import argparse
import datetime
import logging
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Learned from the old account's (throttled) performance numbers. Keeping these would teach
# the new account's analysis the patterns of an account TikTok had already stopped showing.
# Deliberately NOT in this list: top_performers.json. Despite the name it is a curated,
# git-tracked seed file of hook examples, not anything the old account learned.
ARCHIVE_FILES = [
    "viral_memory.json",
    "ai_guidelines.txt",
    "optimization_state.json",
]

# Pure per-account bookkeeping with no value after the switch; the pacing clock in
# particular must not make the new account wait out the old one's interval.
DELETE_FILES = [
    "upload_pacing_state.json",
]

# Playwright profile directories holding the OLD account's logged-in session.
DELETE_DIRS = [
    ".tiktok_browser_profile",
]

# Explicitly preserved — see module docstring.
KEEP_FILES = ["upload_ledger.json", "cookies.json", "top_performers.json"]


def _is_git_tracked(relative_path: str) -> bool:
    """True if git tracks this path. Archiving a tracked file would show up as a deletion in
    the working tree, which is never what an account switch should do — every genuinely
    account-specific state file here is gitignored."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative_path],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _fleet_is_running() -> list:
    """Returns the names of pipeline processes that are currently up."""
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available — cannot verify the fleet is stopped. Make sure it is.")
        return []

    watched = {"process_supervisor.py", "orchestrator.py", "auto_pilot.py", "metrics_tracker.py"}
    running = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for part in cmdline:
            stem = Path(part).name
            if stem in watched:
                running.append(f"{stem} (pid {proc.pid})")
                break
    return running


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset per-account state for a TikTok account switch.")
    parser.add_argument("--dry-run", action="store_true", help="Only print what would change.")
    parser.add_argument("--clear-output", action="store_true",
                        help="Also move rendered clips out of output/ so the new account starts fresh.")
    parser.add_argument("--force", action="store_true",
                        help="Proceed even though pipeline processes appear to be running.")
    args = parser.parse_args()

    running = _fleet_is_running()
    if running and not args.force:
        logger.error(
            "These pipeline processes are still running: %s. Stop the fleet first (dashboard, or "
            "`sudo systemctl stop auto-clipping-supervisor` on the server), then re-run. "
            "Use --force to override.", ", ".join(running),
        )
        return 1

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = REPO_ROOT / f"backup_alter_account/{stamp}"
    prefix = "[dry-run] " if args.dry_run else ""

    if not args.dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for name in ARCHIVE_FILES:
        src = REPO_ROOT / name
        if not src.exists():
            logger.info("%s%s: not present, skipping", prefix, name)
            continue
        if _is_git_tracked(name):
            logger.warning(
                "%s is tracked by git, so it is repository content rather than account state "
                "— leaving it alone.", name,
            )
            continue
        logger.info("%sarchiving %s -> %s", prefix, name, backup_dir.relative_to(REPO_ROOT) / name)
        if not args.dry_run:
            shutil.move(str(src), str(backup_dir / name))
        moved += 1

    for name in DELETE_FILES:
        target = REPO_ROOT / name
        if not target.exists():
            logger.info("%s%s: not present, skipping", prefix, name)
            continue
        logger.info("%sdeleting %s", prefix, name)
        if not args.dry_run:
            target.unlink()

    for name in DELETE_DIRS:
        target = REPO_ROOT / name
        if not target.exists():
            continue
        logger.info("%sdeleting directory %s", prefix, name)
        if not args.dry_run:
            shutil.rmtree(target, ignore_errors=True)

    if args.clear_output:
        output_dir = REPO_ROOT / "output"
        clips = sorted(output_dir.glob("*.mp4")) if output_dir.exists() else []
        if clips:
            parked = backup_dir / "output"
            logger.info("%smoving %d rendered clip(s) out of output/ -> %s", prefix, len(clips),
                        parked.relative_to(REPO_ROOT))
            if not args.dry_run:
                parked.mkdir(parents=True, exist_ok=True)
                for clip in clips:
                    shutil.move(str(clip), str(parked / clip.name))
        else:
            logger.info("%soutput/: no rendered clips to move", prefix)

    for name in KEEP_FILES:
        if (REPO_ROOT / name).exists():
            logger.info("keeping %s (intentionally)", name)

    logger.info("")
    if args.dry_run:
        logger.info("Dry run only — nothing was changed. Re-run without --dry-run to apply.")
        return 0

    logger.info("✅ Per-account state reset. Archive: %s", backup_dir.relative_to(REPO_ROOT))
    logger.info("Next: 1) log into the NEW account in your normal browser and close it,")
    logger.info("      2) python get_cookies.py")
    logger.info("      3) python verify_tiktok_selectors.py --headless")
    if moved:
        logger.info("      (the archived learning files stay on disk — delete them yourself once happy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
