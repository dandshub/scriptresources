"""Zero-copy raw-image backend.

Rescuezilla's raw (dd / BitLocker) partition images are a full-partition
byte stream, gzip-compressed and split into chunks. The default mounter
decompresses the whole thing to a local file before mounting — for a large
Windows volume that can be hundreds of GB of scratch space.

This backend avoids that. gzip is not randomly seekable on its own, so we build
a small seek index once (a single streaming pass — no full local copy), then
serve the *decompressed* stream on demand through a one-file FUSE mount. The
resulting `<mountdir>/image` behaves like the raw partition and can be handed
straight to dislocker / `mount -o ro,loop`, exactly like the restored file — but
the only thing stored locally is the index (tens of MB), not the partition.

Only gzip is supported here (Clonezilla's default and what these images use);
other compressors fall back to the full-restore path in mounter.py.

Requires: fusepy, indexed_gzip, and libfuse on the host.
"""
from __future__ import annotations

import errno
import hashlib
import os
import stat
import threading

from . import config

try:
    import indexed_gzip
    HAVE_GZIP = True
except Exception:  # noqa: BLE001 - optional feature
    HAVE_GZIP = False

try:
    from fuse import FUSE, FuseOSError, Operations
    HAVE_FUSE = True
except Exception:  # noqa: BLE001 - optional feature
    HAVE_FUSE = False
    Operations = object  # placeholder so the class body below is importable

IMAGE_NAME = "image"
# Index spacing: smaller = larger index but faster random reads (less
# re-decompression per seek). 16 MiB is a reasonable balance.
_SPACING = 16 * 1024 * 1024


def available() -> bool:
    """True if the full zero-copy path (index + FUSE mount) can run."""
    return HAVE_GZIP and HAVE_FUSE


class ConcatReader:
    """A read-only seekable file-like spanning ordered chunk files as one stream."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = list(paths)
        self.sizes = [os.path.getsize(p) for p in self.paths]
        self.total = sum(self.sizes)
        # Cumulative start offset of each chunk.
        self.starts = []
        acc = 0
        for s in self.sizes:
            self.starts.append(acc)
            acc += s
        self._pos = 0
        self._fh = None
        self._idx = -1

    def _chunk_for(self, pos: int) -> int:
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _open(self, idx: int) -> None:
        if self._idx != idx:
            if self._fh:
                self._fh.close()
            self._fh = open(self.paths[idx], "rb")
            self._idx = idx

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 1:
            offset += self._pos
        elif whence == 2:
            offset += self.total
        self._pos = offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.total - self._pos
        out = bytearray()
        while n > 0 and 0 <= self._pos < self.total:
            ci = self._chunk_for(self._pos)
            self._open(ci)
            within = self._pos - self.starts[ci]
            self._fh.seek(within)
            want = min(n, self.sizes[ci] - within)
            data = self._fh.read(want)
            if not data:
                break
            out += data
            self._pos += len(data)
            n -= len(data)
        return bytes(out)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def index_path_for(chunks: list[str]) -> str | None:
    """Deterministic cache path for a chunk set's seek index, or None if caching
    is disabled. The key includes each chunk's path, size and mtime, so an image
    that changes on disk gets a fresh index."""
    if not config.INDEX_CACHE:
        return None
    h = hashlib.sha256()
    for p in chunks:
        st = os.stat(p)
        h.update(os.path.abspath(p).encode())
        h.update(f"{st.st_size}:{int(st.st_mtime)}".encode())
    return os.path.join(config.INDEX_DIR, h.hexdigest()[:32] + ".gzidx")


def has_cached_index(chunks: list[str]) -> bool:
    try:
        idx = index_path_for(chunks)
    except OSError:
        return False
    return bool(idx and os.path.exists(idx))


def _export_atomic(gz, idx: str) -> None:
    os.makedirs(os.path.dirname(idx), exist_ok=True)
    tmp = f"{idx}.tmp.{os.getpid()}"
    try:
        gz.export_index(tmp)
        os.replace(tmp, idx)
    except Exception:  # noqa: BLE001 - caching is best-effort
        try:
            os.remove(tmp)
        except OSError:
            pass


def open_decompressed(chunks: list[str]):
    """Return (IndexedGzipFile, uncompressed_size). Imports a cached seek index
    if present; otherwise builds it in one streaming pass and caches it."""
    reader = ConcatReader(chunks)
    gz = indexed_gzip.IndexedGzipFile(fileobj=reader, spacing=_SPACING)
    idx = index_path_for(chunks)
    imported = False
    if idx and os.path.exists(idx):
        try:
            gz.import_index(idx)
            imported = True
        except Exception:  # noqa: BLE001 - corrupt/incompatible cache: rebuild
            imported = False
    if not imported:
        gz.build_full_index()
        if idx:
            _export_atomic(gz, idx)
    gz.seek(0, os.SEEK_END)
    size = gz.tell()
    gz.seek(0)
    return gz, size


class _RawImageFS(Operations):
    """FUSE fs exposing a single read-only file that is the decompressed image."""

    def __init__(self, chunks: list[str]) -> None:
        self._gz, self.size = open_decompressed(chunks)
        self._lock = threading.Lock()
        self._path = "/" + IMAGE_NAME

    def getattr(self, path, fh=None):
        if path == "/":
            return {"st_mode": stat.S_IFDIR | 0o500, "st_nlink": 2}
        if path == self._path:
            return {"st_mode": stat.S_IFREG | 0o400, "st_nlink": 1,
                    "st_size": self.size}
        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        return [".", "..", IMAGE_NAME]

    def open(self, path, flags):
        # Reject writes; this is a read-only view.
        if flags & (os.O_WRONLY | os.O_RDWR):
            raise FuseOSError(errno.EROFS)
        return 0

    def read(self, path, size, offset, fh):
        with self._lock:
            self._gz.seek(offset)
            return self._gz.read(size)


def serve(mountdir: str, chunks: list[str]) -> None:
    """Blocking: mount the FUSE fs at mountdir (call in a dedicated process)."""
    if not available():
        raise RuntimeError("zero-copy requires indexed_gzip and fusepy (+ libfuse)")
    FUSE(_RawImageFS(chunks), mountdir, foreground=True, ro=True,
         nothreads=False, allow_other=False)


if __name__ == "__main__":
    import sys
    # Usage: python -m app.zerocopy <mountdir> <chunk1> [chunk2 ...]
    serve(sys.argv[1], sys.argv[2:])
