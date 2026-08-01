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


def zip_folder(root: str, rel: str) -> Iterator[bytes]:
    """Stream a folder as a zip archive without buffering it all in memory."""
    base = safe_join(root, rel)
    if not os.path.isdir(base):
        raise NotADirectoryError(rel or "/")
    arc_root = os.path.basename(base.rstrip(os.sep)) or "root"

    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                full = os.path.join(dirpath, fn)
                if not os.path.isfile(full) or os.path.islink(full):
                    continue
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
