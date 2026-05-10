#!/usr/bin/env bash
# Boot the HyperFrames Video Pipeline locally.
#
# Usage:
#   ./run.sh                          → http://127.0.0.1:8765 (localhost only)
#   HOST=0.0.0.0 PORT=8000 ./run.sh   → expose to LAN (test from iPad on same Wi-Fi)
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

echo "→ http://${HOST}:${PORT}"
exec .venv/bin/uvicorn server.main:app \
  --host "$HOST" --port "$PORT" \
  --reload --reload-dir server
