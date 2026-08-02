"""Tests for the zero-copy decompression core (ConcatReader + indexed_gzip).

The FUSE mount itself needs libfuse + root and isn't exercised here, but the
part that must be exactly correct — random-access decompression over split gzip
chunks — is fully tested. Skips cleanly if indexed_gzip isn't installed.
"""
import gzip
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import zerocopy  # noqa: E402


def _split_gzip(tmp: str, original: bytes, chunk: int) -> list[str]:
    data = gzip.compress(original)
    paths, i, n = [], 0, 0
    while i < len(data):
        suffix = chr(97 + n // 26) + chr(97 + n % 26)  # aa, ab, ...
        p = os.path.join(tmp, f"img.gz.{suffix}")
        with open(p, "wb") as f:
            f.write(data[i:i + chunk])
        paths.append(p)
        i += chunk
        n += 1
    return paths


def test_concat_reader():
    with tempfile.TemporaryDirectory() as tmp:
        blob = os.urandom(300 * 1024)
        # Write blob split across three files, then read it back as one stream.
        paths = []
        for idx, off in enumerate(range(0, len(blob), 100 * 1024)):
            p = os.path.join(tmp, f"c.{idx}")
            with open(p, "wb") as f:
                f.write(blob[off:off + 100 * 1024])
            paths.append(p)
        r = zerocopy.ConcatReader(paths)
        assert r.total == len(blob)
        assert r.read() == blob
        r.seek(12345)
        assert r.read(500) == blob[12345:12845]
        r.seek(-10, os.SEEK_END)
        assert r.read() == blob[-10:]


def test_random_access_decompression():
    if not zerocopy.HAVE_GZIP:
        print("indexed_gzip not installed; skipping")
        return
    with tempfile.TemporaryDirectory() as tmp:
        original = os.urandom(4 * 1024 * 1024)
        chunks = _split_gzip(tmp, original, 512 * 1024)
        gz, size = zerocopy.open_decompressed(chunks)
        assert size == len(original)
        for _ in range(150):
            off = random.randint(0, len(original) - 1)
            length = random.randint(1, 65536)
            gz.seek(off)
            assert gz.read(length) == original[off:off + length]
        gz.seek(len(original) - 5)
        assert gz.read(100) == original[-5:]


def test_index_cache_build_and_reuse():
    if not zerocopy.HAVE_GZIP:
        print("indexed_gzip not installed; skipping")
        return
    with tempfile.TemporaryDirectory() as tmp:
        # Redirect the cache into the temp dir for this test.
        zerocopy.config.INDEX_CACHE = True
        zerocopy.config.INDEX_DIR = os.path.join(tmp, "idx")
        original = os.urandom(3 * 1024 * 1024)
        chunks = _split_gzip(tmp, original, 512 * 1024)

        idx = zerocopy.index_path_for(chunks)
        assert not os.path.exists(idx)
        assert zerocopy.has_cached_index(chunks) is False

        gz, size = zerocopy.open_decompressed(chunks)   # builds + caches
        assert size == len(original)
        assert os.path.exists(idx)
        assert zerocopy.has_cached_index(chunks) is True

        gz2, size2 = zerocopy.open_decompressed(chunks)  # imports cache
        assert size2 == size
        off = random.randint(0, len(original) - 1)
        gz2.seek(off)
        assert gz2.read(4096) == original[off:off + 4096]
        # Key is stable across calls.
        assert zerocopy.index_path_for(chunks) == idx


if __name__ == "__main__":
    test_concat_reader()
    test_random_access_decompression()
    test_index_cache_build_and_reuse()
    print("ok")
