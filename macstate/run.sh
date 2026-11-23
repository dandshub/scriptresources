#!/usr/bin/env bash
# Start the MacState server.
#   MACSTATE_HOST / MACSTATE_PORT   bind address (default 127.0.0.1:8080)
#   MACSTATE_PUBLIC_URL             URL agents use, e.g. https://macstate.example.com
#   MACSTATE_ADMIN_PASSWORD         initial admin password (first start only)
# See README.md for the full list.
set -euo pipefail
cd "$(dirname "$0")"
PY="${MACSTATE_PYTHON:-}"
for c in "$PY" "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}" .venv/bin/python venv/bin/python python3; do
  [[ -z "$c" ]] && continue
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import uvicorn, fastapi" 2>/dev/null; then PY="$c"; break; fi
  PY=""
done
if [[ -z "$PY" ]]; then
  echo "error: no Python with fastapi+uvicorn found. Run:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if [[ ! -f "${MACSTATE_AGENT_DIR:-build}/VERSION" ]]; then
  echo "warning: agent not built yet; run ./build-agent.sh so Macs can install it" >&2
fi
exec "$PY" -m uvicorn server.main:app --host "${MACSTATE_HOST:-127.0.0.1}" --port "${MACSTATE_PORT:-8080}" --proxy-headers
