"""Installed command line entry points. Each command imports only its own service."""

import argparse
import sys
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

COMMANDS = {
    "supervisor": "autoclip.control.process_supervisor",
    "orchestrator": "autoclip.control.orchestrator",
    "autopilot": "autoclip.control.auto_pilot",
    "metrics": "autoclip.observability.metrics_tracker",
    "check": "autoclip.observability.health_check",
    "audit": "autoclip.quality.clip_quality_audit",
    "guardian": "autoclip.observability.project_guardian",
    "verify-session": "autoclip.tools.verify_tiktok_selectors",
    "cookies": "autoclip.tools.get_cookies",
    "upload-tiktok": "autoclip.publishing.tiktok_uploader",
    "dashboard": "autoclip.dashboard.app",
    "migrate-runtime": "autoclip.tools.migrate_runtime",
    "uploads": "autoclip.control.upload_worker",
    "qc-summary": "autoclip.quality.qc_summary",
    "record": "autoclip.capture.stream_watcher",
    "transcribe": "autoclip.ai.transcribe",
    "analyze": "autoclip.ai.analyze",
    "render": "autoclip.rendering.process",
    "critic": "autoclip.ai.train_loop",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoClip pipeline services")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--version", action="version", version=version("auto-clipping-ai"))
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        parser.parse_args(argv)
        return 0
    args = parser.parse_args(argv[:1])
    from autoclip.runtime import ensure_directories

    if args.command != "migrate-runtime":
        ensure_directories()
    target = COMMANDS[args.command]
    if args.command == "dashboard":
        from streamlit.web import cli

        script = Path(__file__).parent / "dashboard" / "app.py"
        address = (
            [] if any(arg.startswith("--server.address") for arg in argv[1:]) else ["--server.address", "127.0.0.1"]
        )
        sys.argv = ["streamlit", "run", str(script), *address, *argv[1:]]
        return cli.main() or 0
    sys.argv = [target, *argv[1:]]
    result = import_module(target).main()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
