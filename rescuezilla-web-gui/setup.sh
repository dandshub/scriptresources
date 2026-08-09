#!/usr/bin/env bash
# One-shot setup for the Rescuezilla Web GUI on a fresh Debian/Ubuntu host.
# Installs system + Python dependencies into a local .venv. Run after cloning:
#
#   git clone https://github.com/dandshub/scriptresources.git
#   cd scriptresources/rescuezilla-web-gui
#   ./setup.sh
#
# It does NOT start the server or mount an image share — see the printed next
# steps (and README.md) for that.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "error: this script targets Debian/Ubuntu (apt-get not found)." >&2
  echo "       install the deps listed in README.md manually instead." >&2
  exit 1
fi

SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"

echo "==> Installing system packages..."
$SUDO apt-get update
$SUDO apt-get install -y \
  git python3 python3-venv python3-pip \
  partclone ntfs-3g dislocker fuse3 \
  gzip zstd lz4 xz-utils bzip2 lzop \
  cifs-utils pv tmux

# libfuse2 (needed by fusepy for the zero-copy backend) is named differently
# across releases; try both, and don't fail the whole setup if neither applies.
echo "==> Ensuring libfuse2 (for the zero-copy FUSE backend)..."
$SUDO apt-get install -y libfuse2t64 2>/dev/null \
  || $SUDO apt-get install -y libfuse2 2>/dev/null \
  || echo "   note: libfuse2 not found; zero-copy falls back to full decompress."

echo "==> Creating .venv and installing Python dependencies..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

cat <<'EOF'

Setup complete.

Next steps:
  1. Mount the share that holds your Rescuezilla images, e.g.:
       sudo mkdir -p /mnt/dsbh
       sudo mount -t cifs //192.168.1.83/dsbh /mnt/dsbh \
         -o username=YOURUSER,uid=$(id -u),gid=$(id -g),iocharset=utf8

  2. Start it in tmux (survives disconnects), bound to all interfaces:
       tmux new -s rzgui
       sudo -E RZGUI_ADMIN_PASSWORD='ChangeMe123!' \
         RZGUI_IMAGES_DIR=/mnt/dsbh/casey \
         RZGUI_HOST=0.0.0.0 \
         ./run.sh
     (detach with Ctrl-b then d)

  3. Browse to http://<this-host-ip>:8000  — login: admin / the password above.

  Firewall (if ufw is active):  sudo ufw allow 8000/tcp
  EOW BitLocker images additionally need a git dislocker build + RZGUI_DISLOCKER
  (see README.md).
EOF
