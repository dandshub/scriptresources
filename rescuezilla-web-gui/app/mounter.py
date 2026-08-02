"""Mount worker: reconstruct a partition from its partclone image and loop-mount
it read-only.

Pipeline (per partition), run in a background thread because reconstruction of a
large partition can take minutes:

    cat <chunk.aa> <chunk.ab> ... | <decompressor> \
        | partclone.restore -C -q -s - -O <raw> --restore_raw_file
    mount -o ro,loop <raw> <mountpoint>

The resulting raw file is a *sparse* full-size filesystem image (only used
blocks are written), so it consumes roughly the used-data size of the source
partition. `dd` images skip partclone and are decompressed straight to the raw
file.

NOTE: this must run as root (loop mount) on a trusted, dedicated VM. It is not
safe to expose the HTTP service to untrusted networks.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .metadata import DECOMPRESSORS, Partition, parse_image
from .store import store

_STATES = ("pending", "mounting", "mounted", "error", "unmounting", "removed")


@dataclass
class Mount:
    id: str
    image_path: str
    image_name: str
    part: str
    fstype: str
    state: str = "pending"
    message: str = ""
    mountpoint: Optional[str] = None
    raw_file: Optional[str] = None
    dislocker_dir: Optional[str] = None
    created: float = field(default_factory=time.time)


class Mounter:
    def __init__(self) -> None:
        self._mounts: dict[str, Mount] = {}
        self._lock = threading.Lock()
        os.makedirs(config.WORK_DIR, exist_ok=True)
        os.makedirs(config.MOUNT_DIR, exist_ok=True)

    # ---- public API -------------------------------------------------------

    def list(self) -> list[Mount]:
        with self._lock:
            return list(self._mounts.values())

    def get(self, mount_id: str) -> Optional[Mount]:
        with self._lock:
            return self._mounts.get(mount_id)

    def start(self, image_path: str, part: str) -> Mount:
        info = parse_image(image_path)
        partition = next((p for p in info.partitions if p.name == part), None)
        if partition is None:
            raise ValueError(f"partition {part!r} not found in image")
        if not partition.supported:
            raise ValueError(
                f"compressor {partition.compressor!r} not supported "
                f"(need one of {', '.join(sorted(DECOMPRESSORS))})"
            )
        has_key = store.has_bitlocker_key(info.name, part)
        if partition.is_bitlocker and not has_key:
            raise ValueError(
                "partition is BitLocker-encrypted; add a recovery key first"
            )

        # Reuse an existing live mount for the same partition.
        with self._lock:
            for m in self._mounts.values():
                if (
                    m.image_path == info.path
                    and m.part == part
                    and m.state in ("pending", "mounting", "mounted")
                ):
                    return m
            mount_id = uuid.uuid4().hex[:12]
            mount = Mount(
                id=mount_id,
                image_path=info.path,
                image_name=info.name,
                part=part,
                fstype=partition.fstype,
            )
            self._mounts[mount_id] = mount

        threading.Thread(
            target=self._worker, args=(mount_id, partition), daemon=True
        ).start()
        return mount

    def unmount(self, mount_id: str) -> None:
        with self._lock:
            mount = self._mounts.get(mount_id)
            if not mount:
                raise ValueError("unknown mount id")
            mount.state = "unmounting"
        self._teardown(mount)
        with self._lock:
            mount.state = "removed"
            mount.mountpoint = None
            self._mounts.pop(mount_id, None)

    def shutdown(self) -> None:
        for m in self.list():
            try:
                self._teardown(m)
            except Exception:
                pass

    # ---- internals --------------------------------------------------------

    def _set(self, mount_id: str, **kw) -> None:
        with self._lock:
            m = self._mounts.get(mount_id)
            if m:
                for k, v in kw.items():
                    setattr(m, k, v)

    def _worker(self, mount_id: str, partition: Partition) -> None:
        raw = os.path.join(config.WORK_DIR, f"{mount_id}-{partition.name}.raw")
        mnt = os.path.join(config.MOUNT_DIR, mount_id)
        self._set(mount_id, state="mounting", raw_file=raw, message="reconstructing filesystem")
        try:
            self._reconstruct(partition, raw)
            source = raw
            image_name = self.get(mount_id).image_name
            has_key = store.has_bitlocker_key(image_name, partition.name)
            # Detect BitLocker from the reconstructed image itself (reliable),
            # not just from blkid metadata which may be absent.
            bitlocker = (partition.is_bitlocker or has_key
                         or self._looks_like_bitlocker(raw))
            if bitlocker:
                if not has_key:
                    raise RuntimeError(
                        "partition is BitLocker-encrypted — add a recovery key "
                        "under Admin → BitLocker keys, then mount again")
                self._set(mount_id, message="unlocking BitLocker volume")
                dis_dir = os.path.join(config.MOUNT_DIR, f"{mount_id}-dislocker")
                source = self._unlock_bitlocker(
                    image_name, partition.name, raw, dis_dir)
                self._set(mount_id, dislocker_dir=dis_dir)
            os.makedirs(mnt, exist_ok=True)
            self._set(mount_id, message="mounting filesystem")
            self._mount_ro(source, mnt, partition.fstype)
            self._set(mount_id, state="mounted", mountpoint=mnt, message="ready")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self._set(mount_id, state="error", message=str(exc))
            # Best-effort cleanup of a partial raw file.
            try:
                if os.path.exists(raw):
                    os.remove(raw)
            except OSError:
                pass

    def _reconstruct(self, partition: Partition, raw: str) -> None:
        decomp = DECOMPRESSORS[partition.compressor]
        cat = "cat " + " ".join(shlex.quote(f) for f in partition.image_files)
        decomp_cmd = " ".join(shlex.quote(c) for c in decomp)

        # The filename ("-ptcl-img") is not a reliable indicator: Rescuezilla
        # writes partitions it can't read (unsupported fs, BitLocker, etc.) as a
        # RAW dump even though the name still says ptcl. Detect by peeking at the
        # decompressed header for the partclone magic and branch accordingly.
        if self._is_partclone_image(partition, decomp_cmd):
            restore = (
                f"{shlex.quote(config.PARTCLONE_RESTORE)} -C -q -s - "
                f"-O {shlex.quote(raw)} --restore_raw_file"
            )
            pipeline = f"{cat} | {decomp_cmd} | {restore}"
        else:
            # Raw image: the decompressed bytes ARE the (whole-partition) image.
            pipeline = f"{cat} | {decomp_cmd} > {shlex.quote(raw)}"

        proc = subprocess.run(
            ["bash", "-o", "pipefail", "-c", pipeline],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            raise RuntimeError(
                "reconstruction failed:\n" + "\n".join(tail)
            )

    @staticmethod
    def _looks_like_bitlocker(raw: str) -> bool:
        """BitLocker volumes carry the '-FVE-FS-' signature at byte offset 3."""
        try:
            with open(raw, "rb") as fh:
                fh.seek(3)
                return fh.read(8) == b"-FVE-FS-"
        except OSError:
            return False

    @staticmethod
    def _is_partclone_image(partition: Partition, decomp_cmd: str) -> bool:
        """Peek at the decompressed header; True iff it carries partclone's magic.

        partclone image files begin with the ASCII magic 'partclone-image'. Raw
        dumps (dd / encrypted volumes) will not contain it in the first block.
        """
        first = partition.image_files[0]
        peek = f"{decomp_cmd} < {shlex.quote(first)} 2>/dev/null | head -c 8192"
        try:
            out = subprocess.run(["bash", "-c", peek], capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return False
        return b"partclone-image" in out.stdout

    def _unlock_bitlocker(self, image_name: str, part: str, raw: str,
                          dis_dir: str) -> str:
        """Unlock a BitLocker raw image with dislocker; return decrypted image path."""
        key = store.get_bitlocker_key(image_name, part)
        if not key:
            raise RuntimeError("no BitLocker key configured for this partition")
        os.makedirs(dis_dir, exist_ok=True)
        # dislocker flag by key type: recovery password (-p), user password (-u),
        # or BEK startup-key file (-f).
        flag = {"recovery": "-p", "password": "-u", "bek": "-f"}.get(
            key["type"], "-p")
        cmd = ["dislocker-fuse", "-r", "-V", raw, f"{flag}{key['value']}",
               "--", dis_dir]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        decrypted = os.path.join(dis_dir, "dislocker-file")
        if proc.returncode != 0 or not os.path.exists(decrypted):
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"BitLocker unlock failed: {err or 'wrong key?'}")
        return decrypted

    def _mount_ro(self, raw: str, mnt: str, fstype: str) -> None:
        attempts = [["mount", "-o", "ro,loop", raw, mnt]]
        # NTFS (and dd/unknown, often NTFS after dislocker) may need ntfs-3g named.
        if fstype.lower() in ("ntfs", "ntfs3", "dd", ""):
            attempts.append(["mount", "-t", "ntfs-3g", "-o", "ro,loop", raw, mnt])
        last = None
        for cmd in attempts:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                return
            last = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"mount failed: {last}")

    def _teardown(self, mount: Mount) -> None:
        if mount.mountpoint and os.path.ismount(mount.mountpoint):
            subprocess.run(["umount", mount.mountpoint], capture_output=True, text=True)
        if mount.mountpoint and os.path.isdir(mount.mountpoint):
            try:
                shutil.rmtree(mount.mountpoint)
            except OSError:
                pass
        # Release the dislocker FUSE layer (if BitLocker) before dropping the raw.
        if mount.dislocker_dir and os.path.isdir(mount.dislocker_dir):
            subprocess.run(["umount", mount.dislocker_dir], capture_output=True, text=True)
            try:
                shutil.rmtree(mount.dislocker_dir)
            except OSError:
                pass
        if mount.raw_file and os.path.exists(mount.raw_file):
            try:
                os.remove(mount.raw_file)
            except OSError:
                pass


# Module-level singleton used by the web app.
mounter = Mounter()
