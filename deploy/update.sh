#!/usr/bin/env bash
# Maintenance entry point; run as root. Project code always executes as the service user.
# Unit installation is a separate reviewed step; this script does not grant the UI root access.
set -Eeuo pipefail

repo=${AUTOCLIP_DEPLOY_ROOT:-/opt/auto-clipping-ai}
service_user=${AUTOCLIP_SERVICE_USER:-autoclip}
uv_bin=${UV_BIN:-$repo/.maintenance-tools/bin/uv}
if [[ $EUID -ne 0 ]]; then
    echo 'Run: sudo bash deploy/update.sh' >&2
    exit 1
fi
repo=$(realpath "$repo")
cd "$repo"
run_user() { sudo -u "$service_user" env AUTOCLIP_HOME="$repo" UV_PROJECT_ENVIRONMENT="$repo/venv" PLAYWRIGHT_BROWSERS_PATH="$repo/.playwright-browsers" "$@"; }
run_user "$uv_bin" --version
if [[ -n $(run_user git status --porcelain) ]]; then
    echo 'Working tree contains uncommitted changes; update aborted before stopping services.' >&2
    exit 1
fi
install -d -o "$service_user" -g "$service_user" "$repo/var" "$repo/var/backups"
exec 9>"$repo/var/deploy.lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
previous=$(run_user git rev-parse HEAD)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$repo/var/backups/deploy-$stamp"
install -d -o "$service_user" -g "$service_user" "$backup"
printf '%s\n' "$previous" > "$backup/previous-commit.txt"
for unit in auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit; do
    cp "/etc/systemd/system/$unit.service" "$backup/$unit.service"
done
phase=stopping
trap 'echo "Deployment failed during $phase. Services remain stopped; previous commit: $previous; backup: $backup" >&2' ERR
guardian_was_active=false
if systemctl is-active --quiet auto-clipping-guardian; then
    guardian_was_active=true
    systemctl stop auto-clipping-guardian
fi
systemctl stop auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit
phase=fetching
run_user git pull --ff-only
phase=dependencies
run_user "$uv_bin" sync --locked --group dev
run_user "$repo/venv/bin/python" -m playwright install chromium
phase=migration
run_user "$repo/venv/bin/autoclip" migrate-runtime --apply
phase=validation
run_user "$repo/venv/bin/autoclip" check
run_user "$repo/venv/bin/ruff" check .
run_user "$repo/venv/bin/python" -m scripts.check_repository
run_user "$repo/venv/bin/python" -m pytest -q
phase=starting
systemctl restart auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit
systemctl is-active --quiet auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit
if "$guardian_was_active"; then systemctl start auto-clipping-guardian; fi
echo "Deployment complete: $(run_user git rev-parse --short HEAD). Backup: $backup"
