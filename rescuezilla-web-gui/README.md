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
partition. `dd` images skip partclone and are decompressed straight to the raw
file.

## Requirements

System packages (Debian/Ubuntu names):

```
sudo apt install partclone ntfs-3g dislocker \
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
