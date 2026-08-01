"""Parse Clonezilla / Rescuezilla backup image directories.

A Rescuezilla backup is Clonezilla-compatible: it is a *directory* containing
partition-table metadata plus one partclone (or dd) image per partition. The
per-partition image filenames encode the filesystem type and the compressor,
and are usually split into ~4 GB chunks with two-letter suffixes:

    sda1.ext4-ptcl-img.zst.aa
    sda1.ext4-ptcl-img.zst.ab
    sda2.ntfs-ptcl-img.gz
    sda3.dd-img.gz.aa

This module enumerates images in a base directory and, for each image,
enumerates its disks and partitions without touching the (large) data files
beyond a directory listing.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# Anchor is "-ptcl-img" (partclone) or "-dd-img" (raw dd). Compressor is the
# token after the anchor; an optional two-letter split suffix may follow.
_IMG_RE = re.compile(
    r"^(?P<part>[^.]+)\."
    r"(?P<fs>[^.]+)-(?P<method>ptcl|dd)-img"
    r"\.(?P<comp>[^.]+?)"
    r"(?:\.(?P<split>[a-z]{2}))?$"
)

# Map Clonezilla compressor tokens to a streaming "decompress to stdout" command.
DECOMPRESSORS: dict[str, list[str]] = {
    "gz": ["gzip", "-dc"],
    "zst": ["zstd", "-dc"],
    "zstd": ["zstd", "-dc"],
    "lz4": ["lz4", "-dc"],
    "xz": ["xz", "-dc"],
    "bz2": ["bzip2", "-dc"],
    "lzo": ["lzop", "-dc"],
    "lz": ["lzip", "-dc"],
    "uncomp": ["cat"],
    "raw": ["cat"],
}


@dataclass
class Partition:
    name: str                       # e.g. "sda1"
    fstype: str                     # e.g. "ext4", "ntfs", "dd"
    method: str                     # "ptcl" or "dd"
    compressor: str                 # e.g. "zst"
    image_files: list[str] = field(default_factory=list)  # absolute, in order
    uuid: Optional[str] = None
    label: Optional[str] = None
    size_bytes: Optional[int] = None
    is_bitlocker: bool = False

    @property
    def supported(self) -> bool:
        return self.compressor in DECOMPRESSORS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["supported"] = self.supported
        return d


@dataclass
class ImageInfo:
    name: str                       # directory basename
    path: str                       # absolute directory path
    disks: list[str] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "disks": self.disks,
            "partitions": [p.to_dict() for p in self.partitions],
        }


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, "r", errors="replace") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []


def _parse_blkid(image_dir: str) -> dict[str, dict[str, str]]:
    """Return {partname: {"uuid":..., "label":..., "type":...}} from blkid.list."""
    out: dict[str, dict[str, str]] = {}
    for line in _read_lines(os.path.join(image_dir, "blkid.list")):
        # e.g. /dev/sda1: UUID="..." TYPE="ext4" LABEL="root"
        m = re.match(r"^/dev/(\w+):\s*(.*)$", line)
        if not m:
            continue
        part = m.group(1)
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        out[part] = {k.lower(): v for k, v in attrs.items()}
    return out


def _parse_parted_sizes(image_dir: str) -> dict[str, int]:
    """Best-effort partition sizes (bytes) from *-pt.parted files."""
    sizes: dict[str, int] = {}
    try:
        entries = os.listdir(image_dir)
    except OSError:
        return sizes
    for fn in entries:
        if not fn.endswith("-pt.parted"):
            continue
        disk = fn.split("-pt.parted")[0]
        for line in _read_lines(os.path.join(image_dir, fn)):
            # parted "unit B" output: "1      1048576B  524288000B  523239424B  fat32  ..."
            m = re.match(r"^\s*(\d+)\s+\d+B\s+\d+B\s+(\d+)B", line)
            if m:
                sizes[f"{disk}{m.group(1)}"] = int(m.group(2))
    return sizes


def parse_image(image_dir: str) -> ImageInfo:
    """Parse a single image directory into an ImageInfo."""
    name = os.path.basename(os.path.normpath(image_dir))
    info = ImageInfo(name=name, path=os.path.abspath(image_dir))

    disks = _read_lines(os.path.join(image_dir, "disk"))
    info.disks = disks

    blkid = _parse_blkid(image_dir)
    sizes = _parse_parted_sizes(image_dir)

    # Group image chunks by partition.
    grouped: dict[str, dict] = {}
    try:
        entries = sorted(os.listdir(image_dir))
    except OSError:
        entries = []
    for fn in entries:
        m = _IMG_RE.match(fn)
        if not m:
            continue
        part = m.group("part")
        g = grouped.setdefault(
            part,
            {
                "fs": m.group("fs"),
                "method": m.group("method"),
                "comp": m.group("comp"),
                "files": [],
            },
        )
        g["files"].append(os.path.join(info.path, fn))

    for part in sorted(grouped):
        g = grouped[part]
        meta = blkid.get(part, {})
        blk_type = (meta.get("type") or "").lower()
        is_bl = "bitlocker" in blk_type or "bitlocker" in g["fs"].lower()
        info.partitions.append(
            Partition(
                name=part,
                fstype=g["fs"],
                method=g["method"],
                compressor=g["comp"],
                image_files=sorted(g["files"]),  # split suffixes sort correctly
                uuid=meta.get("uuid"),
                label=meta.get("label"),
                size_bytes=sizes.get(part),
                is_bitlocker=is_bl,
            )
        )
    return info


def _looks_like_image(path: str) -> bool:
    try:
        entries = os.listdir(path)
    except OSError:
        return False
    if "parts" in entries or "disk" in entries:
        return True
    return any(_IMG_RE.match(e) for e in entries)


def list_images(base_dir: str) -> list[ImageInfo]:
    """Discover image directories directly under base_dir (and base_dir itself)."""
    images: list[ImageInfo] = []
    base_dir = os.path.abspath(base_dir)
    if _looks_like_image(base_dir):
        images.append(parse_image(base_dir))
    try:
        entries = sorted(os.listdir(base_dir))
    except OSError:
        return images
    for entry in entries:
        full = os.path.join(base_dir, entry)
        if os.path.isdir(full) and _looks_like_image(full):
            images.append(parse_image(full))
    return images
