#!/usr/bin/env bash
# Boot the HyperFrames Video Pipeline server.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r server/requirements.txt
fi

if [ -z "${OPENAI_API_KEY:-}" ] && [ -f .env ]; then
  set -a; source .env; set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "warn: OPENAI_API_KEY is not set — transcription will fail until you export it"
fi

exec .venv/bin/uvicorn server.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8765}" --reload
