"""Shared atomic file-write helpers.

A plain open(path, "w") / Path.write_text() truncates the file before writing the new
content. Several long-running processes in this codebase (auto_pilot.py, orchestrator.py,
streamers.py, train_loop.py, metrics_tracker.py) write shared JSON state files that another
process (app.py, or a sibling daemon) may read concurrently — a kill signal, crash, or power
loss mid-write leaves that file truncated or half-written, and a concurrent reader can land
on a JSONDecodeError mid-write even without a crash.

Every write here goes through write-to-temp-then-os.replace() instead: os.replace() is
atomic on both POSIX and Windows (NTFS via MoveFileEx), so a concurrent reader always sees
either the fully-old or fully-new content, never a partial write.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    directory = path.parent if str(path.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, **json_kwargs) -> None:
    json_kwargs.setdefault("ensure_ascii", False)
    json_kwargs.setdefault("indent", 2)
    atomic_write_text(path, json.dumps(data, **json_kwargs))
