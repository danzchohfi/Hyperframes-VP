#!/usr/bin/env bash
# Expose the local server (127.0.0.1:$PORT) on a public URL via Cloudflare's
# free quick-tunnel — no account needed. Runs only while this script is open.
#
# Use: open one terminal and run ./run.sh, open ANOTHER terminal and run this.
# Cloudflared will print a URL like https://something-random.trycloudflare.com
# Paste that into your iPad's browser.

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8765}"

if ! command -v cloudflared >/dev/null 2>&1; then
  cat <<'EOF' >&2
✗ cloudflared não encontrado.

Instale:
  Mac:    brew install cloudflared
  Linux:  https://pkg.cloudflare.com/  (ou baixe o binário)
  Windows: choco install cloudflared
EOF
  exit 1
fi

if ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health" | grep -q "^200$"; then
  echo "⚠ Nada rodando em http://127.0.0.1:${PORT} — abra OUTRO terminal e rode ./run.sh primeiro." >&2
  exit 1
fi

echo "→ Subindo túnel pra http://127.0.0.1:${PORT}..."
echo "→ A URL pública vai aparecer abaixo. Cole no Safari do iPad."
echo "→ Ctrl-C aqui pra fechar o túnel."
echo
exec cloudflared tunnel --url "http://127.0.0.1:${PORT}"
