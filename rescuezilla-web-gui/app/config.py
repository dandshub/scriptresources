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

# Zero-copy raw backend: "auto" (use when deps present and image is raw+gzip),
# "1"/"on" (force on), "0"/"off" (always full-decompress to a local file).
ZEROCOPY = os.environ.get("RZGUI_ZEROCOPY", "auto").lower()

# Max seconds to wait for the zero-copy seek index to build (one streaming pass
# over the compressed image). Large partitions over SMB can take a while.
ZEROCOPY_TIMEOUT = int(os.environ.get("RZGUI_ZEROCOPY_TIMEOUT", "7200"))

# Persist the zero-copy seek index so repeat mounts of the same image skip the
# rebuild. The index is roughly 0.2% of the decompressed partition size.
INDEX_CACHE = os.environ.get("RZGUI_INDEX_CACHE", "1").lower() not in (
    "0", "off", "false", "no")
INDEX_DIR = os.environ.get("RZGUI_INDEX_DIR", os.path.join(WORK_DIR, "index-cache"))

# dislocker-fuse binary. BitLocker "used-space-only" / Encrypt-On-Write (EOW)
# volumes (the Windows 10/11 default) need a newer dislocker than most distros
# ship — point this at a git build, e.g.
#   RZGUI_DISLOCKER=/home/dan/dislocker-git/src/dislocker-fuse
DISLOCKER = os.environ.get("RZGUI_DISLOCKER", "dislocker-fuse")
