"""Move legacy runtime files into config/ and var/ without overwriting data.

Default is a read-only plan. Stop the fleet before using --apply. Every move is
journalled; a failed move is rolled back before the error is raised.
"""

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import psutil

from autoclip import runtime
from autoclip.core import atomic_io


def plan(home: Path) -> list[tuple[Path, Path]]:
    home = home.resolve()
    names = (
        set(runtime.DIRECTORIES)
        | set(runtime.CONFIG_DIRECTORIES)
        | runtime.STATE_FILES
        | runtime.SECRET_FILES
        | {"streamers.json", "config/instagram_cookies.json"}
    )
    names.update(p.name for p in home.glob("agent_state_*.json"))
    moves = []
    for name in sorted(names):
        source = home / name
        target = runtime.path(name, home=home)
        if not source.exists() or source == target:
            continue
        if not source.resolve().is_relative_to(home) or not target.resolve().is_relative_to(home):
            raise ValueError("Migration path escapes application home")
        if target.exists() and (target.is_file() or any(target.iterdir())):
            raise FileExistsError(f"Both legacy and new paths exist: {name}; resolve before migration")
        moves.append((source, target))
        lock = Path(str(source) + ".lock")
        if lock.exists():
            lock_target = Path(str(target) + ".lock")
            if lock_target.exists():
                raise FileExistsError(f"Both lock paths exist: {name}")
            moves.append((lock, lock_target))
    return moves


def assert_stopped(home: Path) -> None:
    own_pid = __import__("os").getpid()
    markers = ("auto_pilot", "orchestrator", "process_supervisor", "metrics_tracker", "upload_worker", "streamlit")
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.pid == own_pid:
            continue
        try:
            command = " ".join(process.info["cmdline"] or [])
            if any(marker in command for marker in markers) and Path(process.cwd()).resolve() == home.resolve():
                raise RuntimeError("Stop application services before migrating runtime data")
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


def _rewrite_paths(value, home: Path, moves: list[tuple[Path, Path]]):
    if isinstance(value, dict):
        return {key: _rewrite_paths(item, home, moves) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_paths(item, home, moves) for item in value]
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/")
    for source, target in moves:
        for old, new in (
            (source.as_posix(), target.as_posix()),
            (source.relative_to(home).as_posix(), target.relative_to(home).as_posix()),
        ):
            if normalized == old or normalized.startswith(old + "/"):
                return new + normalized[len(old) :]
    return value


def migrate(home: Path, *, check_processes: bool = True) -> dict:
    home = home.resolve()
    if check_processes:
        assert_stopped(home)
    moves = plan(home)  # All collisions checked before the first mutation.
    if not moves:
        return {"status": "already_migrated", "moved": 0}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = home / "var/backups" / f"layout-{stamp}"
    backup.mkdir(parents=True)
    moved = []
    rewritten = []
    manifest = {"status": "in_progress", "moves": [[str(a), str(b)] for a, b in moves]}
    atomic_io.atomic_write_json(backup / "manifest.json", manifest)
    try:
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                target.rmdir()  # Only empty directories passed plan(). Never recursive.
            source.replace(target)
            moved.append((source, target))
        # Retain exact ledger/quota bytes and all credentials. Only media/audit path
        # references and agent telemetry need translating to their new locations.
        for folder in (
            home / "var/state",
            home / "var/quality_audit",
            home / "var/output",
            home / "var/uploaded_clips",
        ):
            for path in folder.rglob("*.json"):
                if path.name in {"upload_ledger.json", "upload_pacing_state.json"}:
                    continue
                try:
                    original = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                updated = _rewrite_paths(original, home, moves)
                if updated != original:
                    saved = backup / "rewritten" / path.relative_to(home)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, saved)
                    rewritten.append((saved, path))
                    atomic_io.atomic_write_json(path, updated)
        runtime.ensure_directories(home=home)
        for name in runtime.SECRET_FILES | {"instagram_cookies.json"}:
            secret = home / "var/secrets" / name
            if secret.exists():
                atomic_io.secure_file_permissions(secret)
        manifest.update(status="complete", moved=len(moved), rewritten=len(rewritten))
        atomic_io.atomic_write_json(backup / "manifest.json", manifest)
        return {
            "status": "complete",
            "moved": len(moved),
            "rewritten": len(rewritten),
            "manifest": str(backup / "manifest.json"),
        }
    except BaseException:
        for saved, target in reversed(rewritten):
            shutil.copy2(saved, target)
        for source, target in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            target.replace(source)
        manifest["status"] = "rolled_back"
        atomic_io.atomic_write_json(backup / "manifest.json", manifest)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=runtime.HOME)
    parser.add_argument("--apply", action="store_true", help="Apply after stopping all application services")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(migrate(args.home), indent=2))
    else:
        print(json.dumps({"dry_run": True, "moves": [[str(a), str(b)] for a, b in plan(args.home)]}, indent=2))
    return 0
