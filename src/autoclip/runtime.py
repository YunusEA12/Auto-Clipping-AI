"""Application home and packaged resources, shared by every entry point.

Set AUTOCLIP_HOME when running outside the deployment checkout. Importing this
module loads no models, starts no processes and creates no directories.
"""

import os
import re
import shutil
from pathlib import Path

from dotenv import load_dotenv

HOME = Path(os.environ.get("AUTOCLIP_HOME", Path.cwd())).resolve()
RESOURCES = Path(__file__).resolve().parent / "resources"

# One registry is shared by the application, migration and deployment checks.
DIRECTORIES = {
    "temp": "var/temp",
    "output": "var/output",
    "uploaded_clips": "var/uploaded_clips",
    "models": "var/models",
    "logs": "var/logs",
    "quality_audit": "var/quality_audit",
    "selector_audit": "var/selector_audit",
    "browser_slots": "var/browser_slots",
    ".tiktok_browser_profile": "var/browser_profile",
    "guardian_reports": "var/guardian_reports",
    "guardian_workspace": "var/guardian_workspace",
    "profile_assets": "var/profile_assets",
    "reports": "var/reports",
    "backup_alter_account": "var/backups/accounts",
}
CONFIG_DIRECTORIES = {"profiles": "config/profiles"}
STATE_FILES = {
    "agent_state.json",
    "ai_guidelines.txt",
    "feedback.json",
    "orchestrator_state.json",
    "fleet_control.json",
    "viral_memory.json",
    "optimization_state.json",
    "chunk_cleanup_state.json",
    "pending_sweep_state.json",
    "gemini_call_budget.json",
    "openai_call_budget.json",
    "youtube_upload_backoff.json",
    "upload_ledger.json",
    "upload_pacing_state.json",
    "metrics_scrape_health.json",
    "guardian_state.json",
    "upload_worker_state.json",
}
SECRET_FILES = {
    "cookies.json",
    "token.json",
    "token.json.bak",
    "client_secret.json",
    "tiktok_client_secret.json",
    "tiktok_token.json",
    "youtube_studio_cookies.json",
    "tiktok_cookies.json",
}


def path(name: str, *, home: Path | None = None) -> Path:
    """Resolve a known application path without depending on the module's location."""
    root = Path(home) if home is not None else HOME
    if name in DIRECTORIES or name in CONFIG_DIRECTORIES:
        return root / (DIRECTORIES | CONFIG_DIRECTORIES)[name]
    if name in STATE_FILES or re.fullmatch(r"agent_state_[\w-]+\.json", name):
        return root / "var/state" / name
    if name in SECRET_FILES:
        return root / "var/secrets" / name
    if name == "config/instagram_cookies.json":
        return root / "var/secrets/instagram_cookies.json"
    if name == "streamers.json":
        return root / "config/streamers.json"
    if name == ".env":
        return root / ".env"
    raise ValueError(f"Unknown application path: {name}")


def ensure_directories(*, home: Path | None = None) -> None:
    root = Path(home) if home is not None else HOME
    for relative in {*DIRECTORIES.values(), *CONFIG_DIRECTORIES.values(), "var/state", "var/secrets", "config"}:
        (root / relative).mkdir(parents=True, exist_ok=True)
    for seed in (RESOURCES / "profiles").glob("*.json"):
        target = root / CONFIG_DIRECTORIES["profiles"] / seed.name
        if not target.exists():
            shutil.copy2(seed, target)
    if os.name != "nt":
        (root / "var/secrets").chmod(0o700)


def load_environment() -> None:
    load_dotenv(HOME / ".env", override=False)
