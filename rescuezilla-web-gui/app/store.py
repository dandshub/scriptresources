"""JSON-backed store for users, per-image sharing ACLs and BitLocker keys.

Prototype-grade persistence: a single JSON file guarded by a process-wide lock.
Passwords are stored as PBKDF2-HMAC-SHA256 hashes. BitLocker recovery keys are
sensitive; the store file must be root-owned and mode 0600 (enforced on write).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from typing import Optional

from . import config

_ITERATIONS = 200_000


def _hash_pw(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return dk.hex()


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = {"users": {}, "acls": {}, "bitlocker_keys": {}, "secret": None}
        self._load()
        self._ensure_secret()
        self._bootstrap_admin()

    # ---- persistence ------------------------------------------------------

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path) as fh:
                    self._data.update(json.load(fh))
            except (OSError, ValueError):
                pass

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self._data, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def _ensure_secret(self) -> None:
        if not self._data.get("secret"):
            self._data["secret"] = secrets.token_hex(32)
            self._save()

    @property
    def secret(self) -> str:
        return self._data["secret"]

    def _bootstrap_admin(self) -> None:
        if self._data["users"]:
            return
        pw = os.environ.get("RZGUI_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
        self.add_user("admin", pw, role="admin")
        if not os.environ.get("RZGUI_ADMIN_PASSWORD"):
            print("=" * 60)
            print(f"  Created initial admin account:  admin / {pw}")
            print("  Change it after first login (or set RZGUI_ADMIN_PASSWORD).")
            print("=" * 60)

    # ---- users ------------------------------------------------------------

    def add_user(self, username: str, password: str, role: str = "user") -> None:
        with self._lock:
            salt = secrets.token_bytes(16)
            self._data["users"][username] = {
                "salt": salt.hex(),
                "hash": _hash_pw(password, salt),
                "role": role,
            }
            self._save()

    def delete_user(self, username: str) -> None:
        with self._lock:
            self._data["users"].pop(username, None)
            for acl in self._data["acls"].values():
                if username in acl:
                    acl.remove(username)
            self._save()

    def set_password(self, username: str, password: str) -> None:
        with self._lock:
            u = self._data["users"].get(username)
            if not u:
                raise ValueError("no such user")
            salt = secrets.token_bytes(16)
            u["salt"], u["hash"] = salt.hex(), _hash_pw(password, salt)
            self._save()

    def verify(self, username: str, password: str) -> Optional[str]:
        """Return the user's role on success, else None."""
        u = self._data["users"].get(username)
        if not u:
            # Constant-ish time: still run a hash to reduce user enumeration.
            _hash_pw(password, b"0" * 16)
            return None
        expected = u["hash"]
        got = _hash_pw(password, bytes.fromhex(u["salt"]))
        if hmac.compare_digest(expected, got):
            return u["role"]
        return None

    def role(self, username: str) -> Optional[str]:
        u = self._data["users"].get(username)
        return u["role"] if u else None

    def list_users(self) -> list[dict]:
        return [{"username": n, "role": u["role"]}
                for n, u in sorted(self._data["users"].items())]

    def is_admin(self, username: str) -> bool:
        return self.role(username) == "admin"

    # ---- image sharing ----------------------------------------------------

    def can_access(self, username: str, image_name: str) -> bool:
        if self.is_admin(username):
            return True
        return username in self._data["acls"].get(image_name, [])

    def share(self, image_name: str, username: str) -> None:
        with self._lock:
            acl = self._data["acls"].setdefault(image_name, [])
            if username not in acl:
                acl.append(username)
            self._save()

    def unshare(self, image_name: str, username: str) -> None:
        with self._lock:
            acl = self._data["acls"].get(image_name, [])
            if username in acl:
                acl.remove(username)
                self._save()

    def shared_with(self, image_name: str) -> list[str]:
        return list(self._data["acls"].get(image_name, []))

    # ---- BitLocker keys ---------------------------------------------------

    @staticmethod
    def _bl_key(image_name: str, part: str) -> str:
        return f"{image_name}/{part}"

    def set_bitlocker_key(self, image_name: str, part: str,
                          value: str, key_type: str = "recovery") -> None:
        with self._lock:
            self._data["bitlocker_keys"][self._bl_key(image_name, part)] = {
                "type": key_type,
                "value": value,
            }
            self._save()

    def get_bitlocker_key(self, image_name: str, part: str) -> Optional[dict]:
        return self._data["bitlocker_keys"].get(self._bl_key(image_name, part))

    def has_bitlocker_key(self, image_name: str, part: str) -> bool:
        return self._bl_key(image_name, part) in self._data["bitlocker_keys"]

    def delete_bitlocker_key(self, image_name: str, part: str) -> None:
        with self._lock:
            self._data["bitlocker_keys"].pop(self._bl_key(image_name, part), None)
            self._save()


store = Store(os.environ.get(
    "RZGUI_STORE", os.path.join(config.WORK_DIR, "rzgui-store.json")))
