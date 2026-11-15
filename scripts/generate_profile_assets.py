"""Prepare the approved ClipMaster assets locally, without API calls or new images."""

import argparse
import json
import shutil
from pathlib import Path

from autoclip import runtime
from autoclip.core import atomic_io

SOURCE = Path(__file__).resolve().parents[1] / "assets/branding"


def prepare(*, force: bool = False) -> Path:
    profile = json.loads((SOURCE / "profile.json").read_text(encoding="utf-8"))
    if len(profile["bio"]) > profile["bio_limit"]:
        raise ValueError("Bio exceeds the configured character limit")
    directory = runtime.path("profile_assets")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "avatar.png"
    if not target.exists() or force:
        if target.exists():
            shutil.copy2(target, directory / "avatar.before-clipmaster.png")
        shutil.copy2(SOURCE / profile["avatar"], target)
    atomic_io.atomic_write_json(directory / "profile.json", profile)
    atomic_io.atomic_write_text(directory / "profile.txt", profile["display_name"] + "\n\n" + profile["bio"] + "\n")
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Replace the prepared avatar, retaining a backup")
    args = parser.parse_args()
    print(prepare(force=args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
