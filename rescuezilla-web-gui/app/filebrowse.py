"""Safe read-only browsing of a mounted filesystem, plus folder zipping."""
from __future__ import annotations

import os
import stat
import zipfile
from typing import Iterator


def safe_join(root: str, rel: str) -> str:
    """Resolve rel under root, refusing anything that escapes root."""
    root = os.path.realpath(root)
    rel = (rel or "").lstrip("/")
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ValueError("path escapes mount root")
    return target


def list_dir(root: str, rel: str) -> list[dict]:
    target = safe_join(root, rel)
    if not os.path.isdir(target):
        raise NotADirectoryError(rel or "/")
    out: list[dict] = []
    with os.scandir(target) as it:
        for entry in it:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            out.append(
                {
                    "name": entry.name,
                    "is_dir": is_dir,
                    "is_symlink": entry.is_symlink(),
                    "size": 0 if is_dir else st.st_size,
                    "mtime": int(st.st_mtime),
                    "mode": stat.filemode(st.st_mode),
                }
            )
    out.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return out


def resolve_file(root: str, rel: str) -> str:
    target = safe_join(root, rel)
    if not os.path.isfile(target):
        raise FileNotFoundError(rel)
    return target


# Windows/NTFS junk and reparse-point names that bloat or loop a recursive zip.
_ZIP_SKIP_NAMES = {
    "$recycle.bin", "system volume information", "pagefile.sys",
    "hiberfil.sys", "swapfile.sys", "dumpstack.log.tmp",
}
# Hard backstop against any cycle that dedup somehow misses.
_ZIP_MAX_DEPTH = 64


def zip_folder(root: str, rel: str) -> Iterator[bytes]:
    """Stream a folder as a zip archive without buffering it all in memory.

    Guards against NTFS junctions / reparse points, hard links and symlink cycles
    (common in Windows profiles, e.g. Chrome's `User Data` and the self-
    referential `Application Data` junctions), which would otherwise re-walk or
    infinitely loop the same content and balloon the archive. Dedup is by
    filesystem identity (device + inode) — the way tar/du do it — because
    ntfs-3g often exposes junctions as ordinary directories that `realpath`
    doesn't resolve.
    """
    base = os.path.realpath(safe_join(root, rel))
    if not os.path.isdir(base):
        raise NotADirectoryError(rel or "/")
    arc_root = os.path.basename(base.rstrip(os.sep)) or "root"

    import io

    def ident(path):
        try:
            st = os.stat(path)          # follows the reparse point / hard link
            return (st.st_dev, st.st_ino)
        except OSError:
            return None

    seen_dirs = set()
    base_id = ident(base)
    if base_id:
        seen_dirs.add(base_id)
    seen_files = set()                  # so a hard-linked file is written once

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for dirpath, dirs, files in os.walk(base, followlinks=False):
            depth = dirpath[len(base):].count(os.sep)
            if depth >= _ZIP_MAX_DEPTH:
                dirs[:] = []
                continue

            # Prune subdirs: junk, symlinks, and any directory whose identity we
            # have already walked (a junction/hard link back into the tree).
            keep = []
            for d in dirs:
                if d.lower() in _ZIP_SKIP_NAMES:
                    continue
                dp = os.path.join(dirpath, d)
                if os.path.islink(dp):
                    continue
                did = ident(dp)
                if did is None or did in seen_dirs:
                    continue            # unreadable, or a cycle/duplicate
                seen_dirs.add(did)
                keep.append(d)
            dirs[:] = keep

            for fn in files:
                if fn.lower() in _ZIP_SKIP_NAMES:
                    continue
                full = os.path.join(dirpath, fn)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                fid = ident(full)
                if fid is not None:
                    if fid in seen_files:
                        continue        # same file via a hard link — write once
                    seen_files.add(fid)
                arcname = os.path.join(arc_root, os.path.relpath(full, base))
                try:
                    zf.write(full, arcname)
                except OSError:
                    continue
                if buf.tell() > 1 << 20:  # flush ~1 MB at a time
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)
    yield buf.getvalue()
