"""NinjaOne integration.

* Syncs NinjaOne organizations -> MacState tenants and NinjaOne Macs ->
  MacState computers (matched by serial number).
* Publishes each tenant's enrollment key to a NinjaOne organization custom
  field, so one NinjaOne script/policy can install the MacState agent on every
  Mac NinjaOne manages (see deploy/ninjaone-install-macstate.sh).
* Can trigger that script on demand for Macs that lack the MacState agent.

Uses NinjaOne Public API v2 with an "API Services (machine-to-machine)"
client-credentials app. Stdlib only.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .db import DB, now
from .security import new_enrollment_key

log = logging.getLogger("macstate.ninjaone")

REGIONS = {
    "https://app.ninjarmm.com": "US (app)",
    "https://us2.ninjarmm.com": "US 2",
    "https://ca.ninjarmm.com": "Canada",
    "https://eu.ninjarmm.com": "EU",
    "https://oc.ninjarmm.com": "Oceania",
}

DEFAULTS = {
    "base_url": "https://app.ninjarmm.com",
    "client_id": "",
    "client_secret": "",
    "scope": "monitoring management",
    "device_classes": "MAC",
    "custom_field": "macstateEnrollmentKey",
    "push_keys": "1",
    "auto_create_tenants": "0",
    "script_id": "",
    "run_as": "system",
    "sync_interval": "3600",
}
SECRET_KEYS = {"client_secret"}
SYNC_LOCK = threading.Lock()


class NinjaError(Exception):
    pass


# ---- settings --------------------------------------------------------------

def get_settings(db: DB) -> dict[str, str]:
    out = dict(DEFAULTS)
    for r in db.all("SELECT key, value FROM settings WHERE key LIKE 'ninja.%'"):
        out[r["key"][6:]] = r["value"]
    return out


def save_settings(db: DB, values: dict[str, Any]) -> None:
    for k, v in values.items():
        if k not in DEFAULTS or v is None:
            continue
        if isinstance(v, bool):
            v = "1" if v else "0"
        v = str(v).strip()
        if k in SECRET_KEYS and v == "":
            continue  # blank secret in the form means "keep current"
        if k == "base_url":
            v = v.rstrip("/")
            if not v.startswith(("https://", "http://")):
                raise ValueError("NinjaOne URL must start with https://")
        if k in ("script_id", "sync_interval") and v and not v.isdigit():
            raise ValueError(f"{k} must be a number")
        db.set_setting(f"ninja.{k}", v)


def public_settings(db: DB) -> dict[str, Any]:
    s = get_settings(db)
    s["client_secret_set"] = bool(s.pop("client_secret"))
    s["configured"] = configured(db)
    s["regions"] = REGIONS
    s["last_sync"] = db.get_setting("ninja_state.last_sync") or ""
    s["last_sync_error"] = db.get_setting("ninja_state.last_error") or ""
    return s


def configured(db: DB) -> bool:
    s = get_settings(db)
    return bool(s["client_id"] and s["client_secret"])


# ---- API client ------------------------------------------------------------

class NinjaClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, scope: str,
                 timeout: float = 30.0) -> None:
        self.base = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.timeout = timeout
        self._token = ""
        self._token_exp = 0.0

    @classmethod
    def from_db(cls, db: DB) -> "NinjaClient":
        s = get_settings(db)
        if not (s["client_id"] and s["client_secret"]):
            raise NinjaError("NinjaOne is not configured (client ID and secret required)")
        return cls(s["base_url"], s["client_id"], s["client_secret"], s["scope"])

    def _open(self, req: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise NinjaError(f"NinjaOne {req.get_method()} {req.full_url} -> HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise NinjaError(f"Cannot reach NinjaOne at {self.base}: {e.reason}") from None
        return json.loads(body) if body.strip() else None

    def token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials", "client_id": self.client_id,
            "client_secret": self.client_secret, "scope": self.scope,
        }).encode()
        req = urllib.request.Request(f"{self.base}/ws/oauth/token", data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "Accept": "application/json"})
        res = self._open(req)
        if not res or "access_token" not in res:
            raise NinjaError("NinjaOne token response had no access_token")
        self._token = res["access_token"]
        self._token_exp = time.time() + float(res.get("expires_in", 3600))
        return self._token

    def request(self, method: str, path: str, params: dict | None = None, body: Any = None) -> Any:
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token()}", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        return self._open(req)

    def paged(self, path: str, params: dict | None = None, page_size: int = 500) -> list[dict]:
        """NinjaOne v2 cursor paging: pageSize + after=<last id>."""
        out: list[dict] = []
        after = None
        while True:
            page = self.request("GET", path, {**(params or {}), "pageSize": page_size, "after": after}) or []
            out.extend(page)
            if len(page) < page_size:
                return out
            after = page[-1]["id"]

    # endpoints
    def organizations(self) -> list[dict]:
        return self.paged("/v2/organizations")

    def mac_devices(self, classes: str) -> list[dict]:
        cls = ",".join(c.strip() for c in classes.split(",") if c.strip()) or "MAC"
        return self.paged("/v2/devices-detailed", {"df": f"class in ({cls})"})

    def set_org_custom_fields(self, org_id: int, fields: dict[str, str]) -> None:
        self.request("PATCH", f"/v2/organization/{org_id}/custom-fields", body=fields)

    def run_script(self, device_id: int, script_id: int, parameters: str, run_as: str) -> None:
        self.request("POST", f"/v2/device/{device_id}/script/run", body={
            "type": "SCRIPT", "id": script_id, "parameters": parameters, "runAs": run_as})


# ---- sync ------------------------------------------------------------------

def _serial(dev: dict) -> str:
    sys_ = dev.get("system") or {}
    return str(sys_.get("serialNumber") or dev.get("serialNumber") or "").strip().upper()


def _os(dev: dict) -> str:
    os_ = dev.get("os") or {}
    name = os_.get("name") or ""
    return f"{name} {os_.get('releaseId') or ''}".strip() if name else ""


def sync(db: DB, client: NinjaClient | None = None) -> dict[str, Any]:
    """Pull orgs + Macs from NinjaOne, link them, push enrollment keys."""
    if not SYNC_LOCK.acquire(blocking=False):
        raise NinjaError("a NinjaOne sync is already running")
    db.set_setting("ninja_state.last_attempt", str(now()))
    try:
        return _sync(db, client or NinjaClient.from_db(db))
    except Exception as e:
        db.set_setting("ninja_state.last_error", f"{time.strftime('%Y-%m-%d %H:%M')}: {e}")
        raise
    finally:
        SYNC_LOCK.release()


def _sync(db: DB, client: NinjaClient) -> dict[str, Any]:
    s = get_settings(db)
    stats = {"organizations": 0, "tenants_linked": 0, "tenants_created": 0, "devices": 0,
             "devices_matched": 0, "keys_pushed": 0, "warnings": []}

    orgs = client.organizations()
    devices = client.mac_devices(s["device_classes"])
    ts = now()

    with db.tx():
        db.run("DELETE FROM ninja_orgs")
        for o in orgs:
            db.run("INSERT INTO ninja_orgs(id,name,description,synced_at) VALUES(?,?,?,?)",
                   (o["id"], o.get("name") or f"Org {o['id']}", o.get("description") or "", ts))
        stats["organizations"] = len(orgs)

        # Link orgs to tenants: existing link, then same name, then optional auto-create.
        linked = {r["ninja_org_id"] for r in db.all("SELECT ninja_org_id FROM tenants WHERE ninja_org_id IS NOT NULL")}
        for o in orgs:
            if o["id"] in linked:
                continue
            t = db.one("SELECT id FROM tenants WHERE ninja_org_id IS NULL AND lower(name)=lower(?)", (o["name"],))
            if t:
                db.run("UPDATE tenants SET ninja_org_id=? WHERE id=?", (o["id"], t["id"]))
                stats["tenants_linked"] += 1
            elif s["auto_create_tenants"] == "1":
                name = o["name"]
                if db.one("SELECT id FROM tenants WHERE lower(name)=lower(?)", (name,)):
                    name = f"{name} (NinjaOne {o['id']})"
                db.run("INSERT INTO tenants(name,enrollment_key,ninja_org_id,created_at) VALUES(?,?,?,?)",
                       (name, new_enrollment_key(), o["id"], ts))
                stats["tenants_created"] += 1
        # Drop links to orgs that no longer exist.
        db.run("UPDATE tenants SET ninja_org_id=NULL WHERE ninja_org_id IS NOT NULL "
               "AND ninja_org_id NOT IN (SELECT id FROM ninja_orgs)")

        db.run("DELETE FROM ninja_devices")
        computers = {c["serial"].upper(): c["id"] for c in db.all("SELECT id, serial FROM computers WHERE serial<>''")}
        db.run("UPDATE computers SET ninja_device_id=NULL")
        for d in devices:
            serial = _serial(d)
            cid = computers.get(serial) if serial else None
            db.run(
                "INSERT INTO ninja_devices(id,organization_id,name,serial,node_class,os_name,offline,"
                "last_contact,computer_id,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (d["id"], d.get("organizationId"), d.get("displayName") or d.get("systemName") or f"Device {d['id']}",
                 serial, d.get("nodeClass") or "", _os(d), int(bool(d.get("offline"))),
                 int(float(d["lastContact"])) if d.get("lastContact") else None, cid, ts))
            if cid:
                db.run("UPDATE computers SET ninja_device_id=? WHERE id=?", (d["id"], cid))
                stats["devices_matched"] += 1
        stats["devices"] = len(devices)

    if s["push_keys"] == "1" and s["custom_field"]:
        for t in db.all("SELECT id,name,enrollment_key,ninja_org_id FROM tenants WHERE ninja_org_id IS NOT NULL"):
            try:
                client.set_org_custom_fields(t["ninja_org_id"], {s["custom_field"]: t["enrollment_key"]})
                stats["keys_pushed"] += 1
            except NinjaError as e:
                stats["warnings"].append(f"{t['name']}: could not set custom field: {e}")

    db.set_setting("ninja_state.last_sync", str(ts))
    db.set_setting("ninja_state.last_error", "; ".join(stats["warnings"])[:2000])
    log.info("NinjaOne sync: %s", stats)
    return stats


def sync_due(db: DB) -> bool:
    if not configured(db):
        return False
    interval = int(get_settings(db)["sync_interval"] or 0)
    if interval <= 0:
        return False
    last = int(db.get_setting("ninja_state.last_attempt") or 0)
    return now() - last >= interval


# ---- agent deployment via NinjaOne ----------------------------------------

def deploy_agent(db: DB, device_ids: list[int], server_url: str,
                 client: NinjaClient | None = None) -> dict[str, Any]:
    """Run the configured NinjaOne install script on the given NinjaOne devices."""
    s = get_settings(db)
    if not s["script_id"].strip().isdigit():
        raise NinjaError("Set the NinjaOne script ID of the MacState install script first")
    client = client or NinjaClient.from_db(db)
    results = {"queued": [], "errors": []}
    for did in device_ids:
        dev = db.one(
            "SELECT d.*, t.enrollment_key, t.name AS tenant_name FROM ninja_devices d "
            "LEFT JOIN tenants t ON t.ninja_org_id=d.organization_id WHERE d.id=?", (did,))
        if not dev:
            results["errors"].append({"device_id": did, "error": "unknown NinjaOne device (sync first)"})
            continue
        if not dev["enrollment_key"]:
            results["errors"].append({"device_id": did, "name": dev["name"],
                                      "error": "its NinjaOne organization is not linked to a tenant"})
            continue
        try:
            client.run_script(did, int(s["script_id"]), f"{server_url} {dev['enrollment_key']}", s["run_as"])
            results["queued"].append({"device_id": did, "name": dev["name"], "tenant": dev["tenant_name"]})
        except NinjaError as e:
            results["errors"].append({"device_id": did, "name": dev["name"], "error": str(e)})
    return results


def link_new_computer(db: DB, computer_id: int, serial: str) -> None:
    """Called on enrollment so a Mac is linked before the next sync."""
    if not serial:
        return
    dev = db.one("SELECT id FROM ninja_devices WHERE serial=?", (serial.strip().upper(),))
    if dev:
        db.run("UPDATE ninja_devices SET computer_id=? WHERE id=?", (computer_id, dev["id"]))
        db.run("UPDATE computers SET ninja_device_id=? WHERE id=?", (dev["id"], computer_id))


def device_url(db: DB, device_id: int) -> str:
    return f"{get_settings(db)['base_url']}/#/deviceDashboard/{device_id}/overview"
