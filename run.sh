#!/usr/bin/env bash
# Boot the HyperFrames Video Pipeline locally.
#
# Usage:
#   ./run.sh                          → http://127.0.0.1:8765 (localhost only)
#   HOST=0.0.0.0 PORT=8000 ./run.sh   → expose to LAN (test from iPad on same Wi-Fi)
#   AUTOPULL=0 ./run.sh               → disable the auto git pull loop
#
# By default the server polls `origin/<current-branch>` every 10s and
# fast-forwards your working tree when a new commit lands. Uvicorn's
# --reload picks up the Python changes; static files are served fresh
# per request, so a browser Cmd+Shift+R is enough to see UI updates.
# Local uncommitted edits suspend auto-pull so nothing is lost.
#
# Edit .env to set OPENAI_API_KEY.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "✗ '$PYTHON' não encontrado. Instale Python 3.11+." >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "✗ ffmpeg não encontrado. Mac: brew install ffmpeg · Linux: apt install ffmpeg" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "⚠ node não encontrado — necessário pra etapa de render (Hyperframes via npx)." >&2
fi

if [ ! -d .venv ]; then
  echo "→ Criando venv..."
  "$PYTHON" -m venv .venv
fi

# Sync deps (cheap when already satisfied).
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r server/requirements.txt

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "→ .env criado a partir de .env.example — edite OPENAI_API_KEY antes de transcrever."
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "⚠ OPENAI_API_KEY não está setado — Whisper vai falhar até você editar .env."
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8765}"

# ── auto-pull (default on) ────────────────────────────────────────────────
# Background loop that fast-forwards the working tree when the remote
# branch advances. Skipped silently if the repo isn't on a tracked branch
# or has uncommitted edits.
AUTOPULL="${AUTOPULL:-1}"
AUTOPULL_INTERVAL="${AUTOPULL_INTERVAL:-10}"
WATCH_PID=""
if [ "$AUTOPULL" = "1" ] && command -v git >/dev/null 2>&1 && [ -d .git ]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
  if [ -n "$BRANCH" ] && [ "$BRANCH" != "HEAD" ]; then
    echo "→ auto-pull on  · origin/${BRANCH} a cada ${AUTOPULL_INTERVAL}s  (AUTOPULL=0 desliga)"
    (
      while sleep "$AUTOPULL_INTERVAL"; do
        # Skip when there are unstaged or staged changes — we never reset over local work.
        if ! git diff --quiet || ! git diff --cached --quiet; then
          continue
        fi
        git fetch --quiet origin "$BRANCH" 2>/dev/null || continue
        LOCAL_SHA="$(git rev-parse HEAD 2>/dev/null || echo)"
        REMOTE_SHA="$(git rev-parse "origin/${BRANCH}" 2>/dev/null || echo)"
        if [ -n "$LOCAL_SHA" ] && [ -n "$REMOTE_SHA" ] && [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
          SHORT_LOCAL="${LOCAL_SHA:0:7}"
          SHORT_REMOTE="${REMOTE_SHA:0:7}"
          MSG="$(git log -1 --format=%s "$REMOTE_SHA" 2>/dev/null || echo)"
          echo
          echo "↓ auto-pull · ${SHORT_LOCAL} → ${SHORT_REMOTE} · ${MSG}"
          git reset --hard --quiet "origin/${BRANCH}"
          # Sync any new Python deps that might have shipped with the commit.
          .venv/bin/pip install --quiet -r server/requirements.txt 2>/dev/null || true
        fi
      done
    ) &
    WATCH_PID=$!
    trap 'kill $WATCH_PID 2>/dev/null || true' EXIT INT TERM
  fi
fi

echo "→ http://${HOST}:${PORT}"
# Long keep-alive so 400 MB+ uploads + minutes-long ffmpeg jobs don't get
# killed mid-flight. Exclude the projects dir from reload watching so
# saving state during a job doesn't restart uvicorn. Short graceful
# shutdown so reloads don't hang for minutes waiting on the SSE stream
# (/events) to close on its own — uvicorn force-kills lingering
# connections after this timeout.
exec .venv/bin/uvicorn server.main:app \
  --host "$HOST" --port "$PORT" \
  --reload --reload-dir server \
  --reload-exclude 'server/projects/*' \
  --timeout-keep-alive 600 \
  --timeout-graceful-shutdown 3
