#!/usr/bin/env bash
# Pull the latest changes and bounce the server. Run this whenever new
# improvements land on the branch.
#
#   ./update.sh
#
# Local data in server/projects/ and your .env are preserved.

set -euo pipefail
cd "$(dirname "$0")"

BRANCH="${BRANCH:-claude/setup-hyperframes-DRZSw}"

if [ -n "$(git status --porcelain)" ]; then
  echo "⚠ Working tree has local changes:"
  git status --short
  echo
  echo "Stash them first (git stash) or commit before pulling."
  exit 1
fi

echo "→ Fetching origin/$BRANCH..."
git fetch origin "$BRANCH"
git checkout "$BRANCH" >/dev/null 2>&1 || git checkout -b "$BRANCH" "origin/$BRANCH"
git pull --ff-only origin "$BRANCH"

# Re-sync deps (run.sh also does this, but doing it here surfaces failures early).
if [ -d .venv ]; then
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r server/requirements.txt
fi

echo "→ Atualizado. Rodar: ./run.sh"
