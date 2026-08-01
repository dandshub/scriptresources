"""Runtime configuration, all overridable via environment variables."""
import os

# Directory containing one or more Rescuezilla/Clonezilla image directories.
IMAGES_DIR = os.environ.get("RZGUI_IMAGES_DIR", "/images")

# Scratch space for the reconstructed sparse raw filesystem images.
WORK_DIR = os.environ.get("RZGUI_WORK_DIR", "/var/lib/rzgui/work")

# Where partitions get mounted (read-only).
MOUNT_DIR = os.environ.get("RZGUI_MOUNT_DIR", "/var/lib/rzgui/mnt")

# Bind host/port for uvicorn (see run.sh).
HOST = os.environ.get("RZGUI_HOST", "127.0.0.1")
PORT = int(os.environ.get("RZGUI_PORT", "8000"))

# partclone.restore binary name (override if installed under a different name).
PARTCLONE_RESTORE = os.environ.get("RZGUI_PARTCLONE", "partclone.restore")
