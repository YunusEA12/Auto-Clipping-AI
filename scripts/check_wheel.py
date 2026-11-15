"""Check a built wheel from outside the repository, using its installed contents."""

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from scripts.check_repository import FORBIDDEN


def main() -> int:
    wheel = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else max(Path("dist").glob("*.whl"), key=lambda p: p.stat().st_mtime).resolve()
    )
    with tempfile.TemporaryDirectory(prefix="autoclip-wheel-") as directory:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            assert all(not FORBIDDEN.search(name) for name in names), "Credential-shaped file inside wheel"
            assert all(not name.startswith(("var/", "tests/", "scripts/")) for name in names)
            assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
            archive.extractall(directory)
        code = (
            "import sys,pathlib; sys.path.insert(0,sys.argv[1]); import autoclip; "
            "from autoclip import runtime; "
            "assert pathlib.Path(autoclip.__file__).is_relative_to(sys.argv[1]); "
            "assert (runtime.RESOURCES/'profiles/default_streamer.json').is_file(); "
            "assert (runtime.RESOURCES/'top_performers.json').is_file(); "
            "from autoclip.__main__ import main; sys.argv=['autoclip','--version']; main()"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code, directory], cwd=directory, capture_output=True, text=True, timeout=30
        )
        if completed.returncode:
            print(completed.stderr)
            return 1
        print(json.dumps({"wheel": wheel.name, "isolated_import": "passed", "version": completed.stdout.strip()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
