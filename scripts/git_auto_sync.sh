#!/usr/bin/env bash
# Daily automated commit+push of tracked code changes to origin/main.
#
# Runs as the `autoclip` user via the git-auto-sync.timer/.service systemd units (see
# SETUP_SERVER.md). Deliberately conservative:
#   - pulls first (--ff-only) so it never pushes on top of a diverged remote
#   - `git add -A` relies on .gitignore to keep session/credential state (streamers.json,
#     cookies.json, token.json, *.mp4, uploaded_clips/, etc. — see .gitignore) out of git
#     entirely; nothing here overrides or second-guesses that.
#   - an extra filename sniff-test on top of .gitignore: refuses to commit if anything about
#     to be staged merely LOOKS like a secret/credential file, in case a new one is ever added
#     to the repo before .gitignore is updated for it. A false positive here just skips the
#     day's sync (logged) rather than risking a real leak.
#   - never force-pushes, never rebases interactively, never touches history.
set -euo pipefail

REPO_DIR="/opt/auto-clipping-ai"
LOG_DIR="$REPO_DIR/logs"
LOG_FILE="$LOG_DIR/git_auto_sync.log"
BRANCH="main"

mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

echo "=== $(date -Is) — git-auto-sync starting ==="
cd "$REPO_DIR"

git fetch origin "$BRANCH"

# Never push on top of a remote that has moved (a manual push, another session) — fast-forward
# only. If this fails, a human needs to look at it; the timer just tries again tomorrow.
if ! git merge --ff-only "origin/$BRANCH"; then
    echo "Local $BRANCH has diverged from origin/$BRANCH — skipping this run, needs manual attention."
    exit 1
fi

if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
    echo "No changes — nothing to sync."
    exit 0
fi

# Sniff-test: filenames that merely look credential-shaped among what's about to be staged,
# beyond whatever .gitignore already excludes. Belt-and-suspenders, not a replacement for
# .gitignore — see this script's own header comment.
SUSPECT_PATTERN='(^|/)(\.env(\..+)?|.*token.*\.json|.*cookies?.*\.json|.*secret.*\.json|.*\.pem|.*\.key)$'
suspects=$(git status --porcelain --untracked-files=all \
    | awk '{print $2}' \
    | grep -viE '\.gitignore$' \
    | grep -EI "$SUSPECT_PATTERN" || true)
if [ -n "$suspects" ]; then
    echo "Refusing to sync — found file(s) that look credential-shaped and are not already gitignored:"
    echo "$suspects"
    exit 1
fi

git add -A

if git diff --cached --quiet; then
    echo "Nothing stageable after add (only ignored/untracked-excluded changes) — nothing to sync."
    exit 0
fi

CHANGED_FILES=$(git diff --cached --name-only | tr '\n' ' ')
git commit -m "chore: automated daily sync ($(date -I))

Auto-committed by scripts/git_auto_sync.sh. Files: $CHANGED_FILES"

git push origin "$BRANCH"
echo "=== $(date -Is) — git-auto-sync done: pushed $(git rev-parse --short HEAD) ==="
