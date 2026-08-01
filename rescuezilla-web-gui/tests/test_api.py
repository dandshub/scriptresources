"""End-to-end API tests for auth, per-user sharing, BitLocker keys and access
control. Uses FastAPI's TestClient; no root or real partclone images needed
(the mount worker itself, which requires root + partclone, is not exercised).

Run:  python3 -m pytest tests/test_api.py
"""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _prepare_env(tmp: str) -> str:
    """Point config at temp store/work/images BEFORE importing the app."""
    images = os.path.join(tmp, "images")
    img = os.path.join(images, "backup-A")
    os.makedirs(img)
    files = {
        "disk": "sda\n",
        "parts": "sda1\n",
        "blkid.list": '/dev/sda1: UUID="X" TYPE="ext4" LABEL="root"\n',
        "sda1.ext4-ptcl-img.gz.aa": "x",
    }
    for fn, c in files.items():
        with open(os.path.join(img, fn), "w") as fh:
            fh.write(c)
    os.environ["RZGUI_STORE"] = os.path.join(tmp, "store.json")
    os.environ["RZGUI_WORK_DIR"] = os.path.join(tmp, "work")
    os.environ["RZGUI_MOUNT_DIR"] = os.path.join(tmp, "mnt")
    os.environ["RZGUI_IMAGES_DIR"] = images
    os.environ["RZGUI_ADMIN_PASSWORD"] = "adminpw"
    return img


def test_auth_sharing_and_access_control():
    with tempfile.TemporaryDirectory() as tmp:
        img = _prepare_env(tmp)
        from fastapi.testclient import TestClient
        from app.main import app

        c = TestClient(app)

        # Unauthenticated requests are rejected.
        assert c.get("/api/images").status_code == 401

        # Admin login + full visibility.
        assert c.post("/api/login",
                      json={"username": "admin", "password": "adminpw"}
                      ).json()["role"] == "admin"
        imgs = c.get("/api/images").json()
        assert [i["name"] for i in imgs["images"]] == ["backup-A"]
        assert imgs["is_admin"] is True

        # Create a normal user and store a BitLocker key.
        assert c.post("/api/admin/users",
                      json={"username": "dan", "password": "danpw",
                            "role": "user"}).status_code == 200
        assert c.post("/api/admin/bitlocker",
                      json={"image_name": "backup-A", "part": "sda1",
                            "value": "123456", "key_type": "recovery"}
                      ).status_code == 200
        assert c.get("/api/images").json()["images"][0]["partitions"][0][
            "has_bitlocker_key"] is True

        # A non-admin user in a fresh session.
        c2 = TestClient(app)
        assert c2.post("/api/login",
                       json={"username": "dan", "password": "danpw"}
                       ).status_code == 200
        assert c2.get("/api/admin/users").status_code == 403        # not admin
        assert c2.get("/api/images").json()["images"] == []          # nothing shared
        assert c2.post("/api/mount",
                       json={"image_path": img, "part": "sda1"}
                       ).status_code == 403                          # unshared

        # Share, then dan can see it.
        assert c.post("/api/admin/share",
                      json={"image_name": "backup-A", "username": "dan"}
                      ).status_code == 200
        assert [i["name"] for i in c2.get("/api/images").json()["images"]] == \
            ["backup-A"]

        # Path-traversal guard and bad credentials.
        assert c2.post("/api/mount",
                       json={"image_path": "/etc", "part": "sda1"}
                       ).status_code == 400
        assert c2.post("/api/login",
                       json={"username": "dan", "password": "nope"}
                       ).status_code == 401

        # Secret store is not world-readable.
        mode = stat.S_IMODE(os.stat(os.environ["RZGUI_STORE"]).st_mode)
        assert mode == 0o600


if __name__ == "__main__":
    test_auth_sharing_and_access_control()
    print("ok")
