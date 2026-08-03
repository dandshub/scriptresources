# Rescuezilla Web GUI (prototype)

A small FastAPI web app for a **dedicated Linux VM** that lets you point at
Rescuezilla / Clonezilla backup images, mount individual partitions read-only,
browse their contents in a browser, and download individual files or whole
folders (as a `.zip`).

It reconstructs a partition from its `partclone` image on demand, loop-mounts it
read-only, and serves a file browser — so you can restore single files without
restoring the whole disk.

Multi-user: username/password login, an admin panel to manage users, per-image
sharing (each user only sees images shared with them; admins see all), and
BitLocker recovery-key storage so encrypted Windows partitions can be unlocked
and browsed.

> ⚠️ **Security.** This service loop-mounts filesystem images and therefore must
> run as **root** on a **trusted, isolated VM**. Do not expose it to untrusted
> networks. It binds to `127.0.0.1` by default; put it behind an SSH tunnel or a
> reverse proxy with authentication if you need remote access.

## How it works

A Rescuezilla backup is a *directory* (Clonezilla-compatible) containing
partition-table metadata plus one `partclone` (or `dd`) image per partition,
split into ~4 GB chunks and compressed. Filenames encode the filesystem and
compressor, e.g. `sda1.ext4-ptcl-img.zst.aa`.

For each mount request the worker runs, in a background thread:

```
cat sda1.ext4-ptcl-img.zst.* | zstd -dc \
  | partclone.restore -C -q -s - -O <raw> --restore_raw_file
mount -o ro,loop <raw> <mountpoint>
```

`--restore_raw_file` produces a **sparse** full-size filesystem image (only used
blocks are written), so scratch usage is roughly the *used* data of the source
partition. Raw images (partitions Rescuezilla couldn't read with partclone —
unsupported filesystems, BitLocker volumes) are handled separately (see below).

Whether a partition is a partclone image or a raw dump is detected at mount time
by peeking at the decompressed header for the partclone magic — the filename is
not reliable (Rescuezilla names raw dumps `*.dd-ptcl-img.*` too).

### Zero-copy raw backend

A full raw partition (e.g. a large BitLocker Windows volume) would otherwise be
decompressed to a full-size local file. Instead, when `indexed_gzip` + `fusepy`
(+ libfuse) are available and the image is gzip, the raw path builds a small
gzip **seek index** in a single streaming pass (no full local copy — only the
index, tens of MB, is stored) and serves the decompressed partition through a
one-file FUSE mount. That FUSE file is handed to dislocker / `mount -o ro,loop`
exactly like a restored file.

Trade-offs: the index build still reads the whole compressed image once (over
the network, if it's on a share), and random reads re-decompress up to the index
spacing (16 MiB) — so it trades local disk for some CPU/latency. Controlled by
`RZGUI_ZEROCOPY` (`auto` | `on` | `off`); non-gzip images always fall back to a
full local decompression.

The seek index is **cached** (under `RZGUI_INDEX_DIR`, default
`$WORK_DIR/index-cache`), keyed by the chunk set's paths/sizes/mtimes, so repeat
mounts of the same partition skip the rebuild and start in seconds. The index
file is roughly **0.2% of the decompressed partition size** (≈ 900 MB for a
~450 GB volume) — far smaller than a full copy, but budget for it on the local
disk. Disable with `RZGUI_INDEX_CACHE=0`.

### BitLocker

BitLocker volumes are detected from the reconstructed image's `-FVE-FS-`
signature and unlocked with `dislocker` using a stored key (Admin → BitLocker
keys). Without a key, the mount fails with a clear message telling you to add
one.

**Used-space-only / Encrypt-On-Write (EOW) volumes** (the Windows 10/11 default)
are *not* supported by most distro dislocker packages (0.7.x) or by `cryptsetup`
— they fail with "Cannot parse volume header" / "EOW information … failed" or
"encrypt-on-write cannot be activated". Build dislocker from git and point the
app at it:

```
sudo apt install -y git cmake gcc make pkg-config libfuse3-dev python3-jinja2 python3-jsonschema
# mbedtls 3 (Debian/Ubuntu don't ship its CMake config):
git clone --branch v3.6.2 --depth 1 https://github.com/Mbed-TLS/mbedtls.git ~/mbedtls
cd ~/mbedtls && git submodule update --init && cmake -DUSE_SHARED_MBEDTLS_LIBRARY=On -DENABLE_TESTING=Off . \
  && make -j"$(nproc)" && sudo make install && sudo ldconfig
git clone https://github.com/Aorimn/dislocker.git ~/dislocker-git
cd ~/dislocker-git && cmake . && make
# then run the app with:
export RZGUI_DISLOCKER=$HOME/dislocker-git/src/dislocker-fuse
```

## Requirements

System packages (Debian/Ubuntu names):

```
sudo apt install partclone ntfs-3g dislocker fuse3 \
     gzip zstd lz4 xz-utils bzip2 lzop lzip   # decompressors you expect to need
```

- `partclone` provides `partclone.restore` (needs `--restore_raw_file` support;
  any reasonably recent version has it).
- `ntfs-3g` for read-only NTFS mounts. ext2/3/4 and vfat/fat use in-kernel drivers.
- `dislocker` unlocks BitLocker volumes (only needed if you have BitLocker images).
- Only the decompressors matching your images' compressor are required at
  mount time; the UI marks partitions with a missing/unsupported compressor as
  non-mountable.

Python:

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

## Run

```
sudo RZGUI_IMAGES_DIR=/mnt/backups/rescuezilla ./run.sh
# then open http://127.0.0.1:8000
```

On first start, if no users exist, an `admin` account is created. Set its
password with `RZGUI_ADMIN_PASSWORD=...`; if you don't, a random one is
generated and printed to the console. Log in as `admin`, then use the **Admin**
panel to add users, share images, and store BitLocker keys.

### Running detached / as a service

Mounts and index builds run inside the **server process**, not the browser — so
you can close the browser (or disconnect) and a long index build keeps going;
reopen and log back in to check progress and download files. Only the server
needs to stay up.

- **Quick:** run it inside `tmux`/`screen` and detach (`Ctrl-b d`), so it
  survives your SSH session closing.
- **Permanent:** install the systemd unit in `deploy/`:
  ```
  sudo cp deploy/rzgui.conf.example /etc/rzgui.conf     # then edit paths/password
  sudo cp deploy/rzgui.service /etc/systemd/system/rzgui.service
  sudo systemctl daemon-reload && sudo systemctl enable --now rzgui
  sudo systemctl status rzgui        # journalctl -u rzgui -f  for logs
  ```
  Edit the `WorkingDirectory`/`ExecStart` paths in the unit if your checkout
  isn't at `/home/dan/scriptresources/rescuezilla-web-gui`. For boot-time start,
  make sure the SMB image share is in `/etc/fstab` (with `_netdev`) so it's
  mounted before the service starts.

Note: active mounts are tracked in memory, so restarting the service drops them
(the reconstructed files/indexes on disk remain; just remount). The index cache
means a remount after a restart is still fast.

Environment variables:

| var                | default                  | meaning                              |
|--------------------|--------------------------|--------------------------------------|
| `RZGUI_IMAGES_DIR` | `/images`                | dir holding image directories        |
| `RZGUI_WORK_DIR`   | `/var/lib/rzgui/work`    | scratch for reconstructed raw images |
| `RZGUI_MOUNT_DIR`  | `/var/lib/rzgui/mnt`     | read-only mount points               |
| `RZGUI_HOST`       | `127.0.0.1`              | bind address                         |
| `RZGUI_PORT`       | `8000`                   | bind port                            |
| `RZGUI_PARTCLONE`  | `partclone.restore`      | partclone binary name/path           |
| `RZGUI_STORE`      | `$WORK_DIR/rzgui-store.json` | user/ACL/key store (mode 0600)   |
| `RZGUI_ADMIN_PASSWORD` | *(random)*           | initial admin password on first run  |
| `RZGUI_ZEROCOPY`   | `auto`                   | zero-copy raw backend: `auto`/`on`/`off` |
| `RZGUI_ZEROCOPY_TIMEOUT` | `7200`             | max seconds to build the seek index  |
| `RZGUI_INDEX_CACHE` | `1`                     | cache the seek index for reuse (`0` to disable) |
| `RZGUI_INDEX_DIR`  | `$WORK_DIR/index-cache`  | where cached seek indexes are stored |
| `RZGUI_DISLOCKER`  | `dislocker-fuse`         | dislocker-fuse binary (point at a git build for EOW volumes) |

`RZGUI_IMAGES_DIR` may point either at a single image directory or at a parent
directory that contains several.

## HTTP API

| method | path                   | purpose                                  |
|--------|------------------------|------------------------------------------|
| GET    | `/api/images`          | list images + partitions                 |
| POST   | `/api/mount`           | `{image_path, part}` → start a mount job |
| GET    | `/api/mounts`          | mount states (`pending`/`mounting`/`mounted`/`error`) |
| POST   | `/api/unmount`         | `{id}` → umount + clean scratch          |
| GET    | `/api/browse`          | `?id=&path=` directory listing           |
| GET    | `/api/download`        | `?id=&path=` single file                 |
| GET    | `/api/download-folder` | `?id=&path=` folder as streamed `.zip`   |
| POST   | `/api/login` / `/api/logout` | session auth                       |
| GET    | `/api/me`              | current user + role                      |
| *admin* | `/api/admin/users` (GET/POST), `/api/admin/users/password`, `/api/admin/users/delete` | user management |
| *admin* | `/api/admin/share`, `/api/admin/unshare` | per-image sharing        |
| *admin* | `/api/admin/bitlocker`, `/api/admin/bitlocker/delete` | BitLocker keys |

All non-auth endpoints require a session; the `/api/admin/*` endpoints require
the `admin` role. Users only see (and can mount/browse) images shared with them.

## Tests

`tests/test_metadata.py` builds a synthetic image directory and checks the
metadata parser (filesystem/compressor/split detection, blkid + size
enrichment). No root or real images needed:

```
python3 -m pytest tests/            # or: python3 tests/test_metadata.py
```

## Status & known limitations (prototype)

- Reconstruct-to-sparse-raw is the simplest, most portable backend. A future
  optimization is `partclone-nbd` (what Rescuezilla's own Image Explorer uses)
  to serve the partclone image as an NBD device with no scratch file.
- Filesystem coverage is whatever the host kernel + `ntfs-3g` can mount:
  ext2/3/4, NTFS, vfat/exFAT work out of the box. BitLocker is unlocked via
  `dislocker` using a stored recovery key/password/BEK. LVM, LUKS, and Windows
  dynamic disks are **not** handled yet.
- Auth is session-cookie based (PBKDF2-hashed passwords). It's a single-process
  prototype: sessions and mounts live in memory, so a restart logs everyone out
  and orphans any active mounts. Put it behind HTTPS (reverse proxy) in use —
  set `same_site`/`https_only` appropriately if you do.
- BitLocker recovery keys are stored in the JSON store (mode 0600, root-owned).
  Treat that file as a secret; for stricter setups, back it with a real secrets
  manager instead.
- Mounts are tracked in-process; restarting the server orphans mounts (unmount
  them manually, or add a startup reconciler).
- Single-user assumption; no authentication layer is included by design.
