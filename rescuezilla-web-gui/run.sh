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

# Find a Python with uvicorn. sudo resets PATH and won't see an activated venv,
# so check explicit locations. Order: RZGUI_PYTHON override, an active venv
# (passed through with `sudo -E`), a venv in this dir, one dir up, then system.
CANDIDATES=(
  "${RZGUI_PYTHON:-}"
  "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}"
  ".venv/bin/python"
  "venv/bin/python"
  "../.venv/bin/python"
  "../venv/bin/python"
  "python3"
)
PY=""
for c in "${CANDIDATES[@]}"; do
  [[ -z "$c" ]] && continue
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import uvicorn" 2>/dev/null; then
    PY="$c"
    break
  fi
done

if [[ -z "$PY" ]]; then
  echo "error: could not find a Python with uvicorn installed." >&2
  echo "       activate your venv and run: pip install -r requirements.txt" >&2
  echo "       then start with:  sudo -E ./run.sh   (preserves the venv)" >&2
  echo "       or set RZGUI_PYTHON=/path/to/venv/bin/python" >&2
  exit 1
fi

exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
