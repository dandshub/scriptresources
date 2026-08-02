"""Tests for the image-directory metadata parser (no root / real images needed)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.metadata import list_images, parse_image  # noqa: E402


def _make_image(root: str) -> str:
    d = os.path.join(root, "2026-08-01-img")
    os.makedirs(d)
    files = {
        "disk": "sda\n",
        "parts": "sda1 sda2 sda3\n",
        "blkid.list": (
            '/dev/sda1: UUID="AAAA" TYPE="vfat" LABEL="EFI"\n'
            '/dev/sda2: UUID="BBBB" TYPE="ext4" LABEL="root"\n'
            '/dev/sda3: TYPE="BitLocker"\n'
        ),
        "sda-pt.parted": (
            "1      1048576B    524288000B   523239424B  fat32\n"
            "2      524288001B  10000000000B 9475712000B ext4\n"
        ),
        # Split, zstd-compressed ext4 image + single-chunk vfat + a dd/bitlocker part.
        "sda1.vfat-ptcl-img.gz": "x",
        "sda2.ext4-ptcl-img.zst.aa": "x",
        "sda2.ext4-ptcl-img.zst.ab": "x",
        "sda3.ntfs-ptcl-img.zst.aa": "x",
    }
    for name, content in files.items():
        with open(os.path.join(d, name), "w") as fh:
            fh.write(content)
    return d


def test_parse_image():
    with tempfile.TemporaryDirectory() as tmp:
        d = _make_image(tmp)
        info = parse_image(d)
        assert info.disks == ["sda"]
        parts = {p.name: p for p in info.partitions}
        assert set(parts) == {"sda1", "sda2", "sda3"}

        assert parts["sda1"].fstype == "vfat"
        assert parts["sda1"].compressor == "gz"
        assert len(parts["sda1"].image_files) == 1
        assert parts["sda1"].label == "EFI"
        assert parts["sda1"].size_bytes == 523239424

        # Split chunks are collected and ordered.
        assert parts["sda2"].fstype == "ext4"
        assert parts["sda2"].compressor == "zst"
        assert [os.path.basename(f) for f in parts["sda2"].image_files] == [
            "sda2.ext4-ptcl-img.zst.aa",
            "sda2.ext4-ptcl-img.zst.ab",
        ]
        assert parts["sda2"].supported is True

        # BitLocker detected from blkid.
        assert parts["sda3"].is_bitlocker is True


def test_list_images():
    with tempfile.TemporaryDirectory() as tmp:
        _make_image(tmp)
        images = list_images(tmp)
        assert len(images) == 1
        assert images[0].name == "2026-08-01-img"


if __name__ == "__main__":
    test_parse_image()
    test_list_images()
    print("ok")
