"""process_supervisor.py used to be named watchdog.py, which silently shadowed the
third-party `watchdog` package (a Streamlit file-watcher dependency) for every script run
from this project's root — since the current directory takes precedence on sys.path,
`import watchdog` anywhere in the project resolved to our file instead of the real package,
breaking `streamlit run app.py` outright. Caught by actually launching Streamlit, not by
reasoning about it — these tests are the regression guard against reintroducing a module
name that collides with a real installed package."""

import subprocess
import sys

import process_supervisor


def test_watchdog_import_resolves_to_the_real_package_not_ours():
    import watchdog
    assert "site-packages" in watchdog.__file__.replace("\\", "/")
    assert watchdog.__file__ != process_supervisor.__file__


def test_no_module_named_watchdog_in_this_project():
    from pathlib import Path
    project_root = Path(process_supervisor.__file__).parent
    assert not (project_root / "watchdog.py").exists()


def test_crash_backoff_still_works_under_the_new_name():
    delays = [process_supervisor._crash_backoff_seconds(streak) for streak in (1, 2, 3)]
    assert delays == sorted(delays)


def test_streamlit_actually_imports_its_own_file_watcher_dependency():
    from pathlib import Path

    # The concrete failure mode only reproduces when run from the project root (that's what
    # put our file on sys.path ahead of site-packages) — cwd is set explicitly so this test
    # actually exercises that condition, not just wherever pytest happens to be invoked from.
    project_root = Path(process_supervisor.__file__).parent
    result = subprocess.run(
        [sys.executable, "-c", "from streamlit.watcher.event_based_path_watcher import EventBasedPathWatcher"],
        capture_output=True, text=True, timeout=30, cwd=project_root,
    )
    assert result.returncode == 0, result.stderr
