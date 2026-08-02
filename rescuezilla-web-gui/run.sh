#!/usr/bin/env bash
# Launch the Rescuezilla Web GUI. Must run as root (loop mounts).
#
#   RZGUI_IMAGES_DIR   directory holding one or more image directories (default /images)
#   RZGUI_WORK_DIR     scratch for reconstructed raw images (default /var/lib/rzgui/work)
#   RZGUI_MOUNT_DIR    mount points (default /var/lib/rzgui/mnt)
#   RZGUI_HOST/PORT    bind address (default 127.0.0.1:8000)
set -euo pipefail
cd "$(dirname "$0")"

if [[ $EUID -ne 0 ]]; then
  echo "warning: not running as root; loop mounts will fail." >&2
fi

HOST="${RZGUI_HOST:-127.0.0.1}"
PORT="${RZGUI_PORT:-8000}"

# Prefer a local venv (sudo resets PATH and won't see an activated venv).
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PY="venv/bin/python"
else
  PY="python3"
fi

if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "error: uvicorn not installed for $PY" >&2
  echo "       run: ${PY%/python}/pip install -r requirements.txt" >&2
  echo "       (or if using the system python: pip install -r requirements.txt)" >&2
  exit 1
fi

exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
