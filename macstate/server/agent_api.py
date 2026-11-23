"""Endpoints used by the macOS agent: enrollment, check-in, result reporting,
plus the install script and agent binary downloads."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import config, engine
from .db import db, dumps, now
from .security import new_token, token_hash

router = APIRouter()

ARCHES = ("arm64", "amd64")


# ---- agent binaries --------------------------------------------------------

def agent_binary(arch: str) -> str:
    return os.path.join(config.AGENT_DIR, f"macstate-agent-darwin-{arch}")


def agent_build_info() -> dict:
    """Version + per-arch sha256 of the binaries in AGENT_DIR (cached by mtime)."""
    info = {"version": "", "sha256": {}}
    vfile = os.path.join(config.AGENT_DIR, "VERSION")
    if os.path.exists(vfile):
        with open(vfile) as fh:
            info["version"] = fh.read().strip()
    for arch in ARCHES:
        path = agent_binary(arch)
        if os.path.exists(path):
            key = (path, os.path.getmtime(path))
            if key not in _hash_cache:
                h = hashlib.sha256()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                _hash_cache[key] = h.hexdigest()
            info["sha256"][arch] = _hash_cache[key]
    return info


_hash_cache: dict[tuple, str] = {}


def public_url(request: Request) -> str:
    return config.PUBLIC_URL or str(request.base_url).rstrip("/")


@router.get("/agent/download/{arch}")
def download_agent(arch: str) -> FileResponse:
    if arch not in ARCHES or not os.path.exists(agent_binary(arch)):
        raise HTTPException(404, "agent binary not built; run build-agent.sh on the server")
    return FileResponse(agent_binary(arch), media_type="application/octet-stream",
                        filename=f"macstate-agent-{arch}")


@router.get("/agent/install.sh", response_class=PlainTextResponse)
def install_script(key: str, request: Request) -> str:
    tenant = db.one("SELECT id FROM tenants WHERE enrollment_key=?", (key,))
    if not tenant:
        raise HTTPException(404, "unknown enrollment key")
    tmpl_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deploy", "install-agent.sh")
    with open(tmpl_path) as fh:
        tmpl = fh.read()
    return (tmpl.replace("__SERVER__", shlex.quote(public_url(request)))
                .replace("__KEY__", shlex.quote(key)))


# ---- auth ------------------------------------------------------------------

def current_computer(authorization: str = Header("")) -> dict:
    m = re.match(r"Bearer\s+(\S+)", authorization or "")
    if not m:
        raise HTTPException(401, "missing agent token")
    comp = db.one("SELECT * FROM computers WHERE token_hash=?", (token_hash(m.group(1)),))
    if not comp:
        raise HTTPException(401, "invalid agent token")
    return comp


# ---- models ----------------------------------------------------------------

class Facts(BaseModel):
    hostname: str = ""
    serial: str = ""
    model: str = ""
    os_version: str = ""
    arch: str = ""
    agent_version: str = ""
    console_user: str = ""
    ip_address: str = ""


class EnrollRequest(Facts):
    enrollment_key: str


class CheckinRequest(Facts):
    inventory: dict | None = None


class ActionReport(BaseModel):
    status: str
    detected_before: str = ""
    detected_after: str = ""
    message: str = ""
    log: list[str] = Field(default_factory=list)


class FinishRequest(BaseModel):
    message: str = ""


def _update_facts(computer_id: int, f: Facts, request: Request) -> None:
    ip = f.ip_address or (request.client.host if request.client else "")
    db.run(
        "UPDATE computers SET name=COALESCE(NULLIF(?,''),name), serial=COALESCE(NULLIF(?,''),serial), "
        "model=COALESCE(NULLIF(?,''),model), os_version=COALESCE(NULLIF(?,''),os_version), "
        "arch=COALESCE(NULLIF(?,''),arch), agent_version=COALESCE(NULLIF(?,''),agent_version), "
        "console_user=?, ip_address=?, last_seen=? WHERE id=?",
        (f.hostname, f.serial, f.model, f.os_version, f.arch, f.agent_version,
         f.console_user, ip, now(), computer_id),
    )


# ---- endpoints -------------------------------------------------------------

@router.post("/api/agent/enroll")
def enroll(req: EnrollRequest, request: Request) -> dict:
    tenant = db.one("SELECT * FROM tenants WHERE enrollment_key=?", (req.enrollment_key,))
    if not tenant:
        raise HTTPException(403, "invalid enrollment key")
    token = new_token()
    existing = None
    if req.serial:
        existing = db.one("SELECT * FROM computers WHERE serial=? AND tenant_id=?",
                          (req.serial, tenant["id"]))
    if existing:
        # Re-install on a known Mac: rotate its token, keep history.
        db.run("UPDATE computers SET token_hash=? WHERE id=?", (token_hash(token), existing["id"]))
        cid = existing["id"]
    else:
        cid = db.run(
            "INSERT INTO computers(tenant_id,name,serial,token_hash,created_at) VALUES(?,?,?,?,?)",
            (tenant["id"], req.hostname or req.serial or "Mac", req.serial, token_hash(token), now()),
        )
    _update_facts(cid, req, request)
    comp = db.one("SELECT * FROM computers WHERE id=?", (cid,))
    if not existing and tenant["onboarding_enabled"]:
        engine.create_session(db, comp, "onboarding", "apply", created_by="enrollment")
    return {"computer_id": cid, "token": token, "tenant": tenant["name"]}


def _session_payload(session: dict) -> dict:
    actions = db.all(
        "SELECT * FROM session_actions WHERE session_id=? AND status IN ('pending','running') ORDER BY seq",
        (session["id"],))
    return {
        "id": session["id"],
        "mode": session["mode"],
        "kind": session["kind"],
        "actions": [
            {"id": a["id"], "type": a["item_type"], "name": a["name"], **json.loads(a["spec_json"])}
            for a in actions
        ],
    }


@router.post("/api/agent/checkin")
def checkin(req: CheckinRequest, request: Request, comp: dict = Depends(current_computer)) -> dict:
    _update_facts(comp["id"], req, request)
    if req.inventory is not None:
        db.run("UPDATE computers SET inventory_json=?, inventory_at=? WHERE id=?",
               (dumps(req.inventory), now(), comp["id"]))

    payload: dict = {"poll_seconds": config.POLL_SECONDS, "session": None}
    session = engine.open_session(db, comp["id"])
    if session:
        if session["status"] == "pending":
            db.run("UPDATE sessions SET status='running', started_at=?, updated_at=? WHERE id=?",
                   (now(), now(), session["id"]))
            engine.log(db, session["id"], None, "Agent picked up session")
            session["status"] = "running"
        else:
            engine.log(db, session["id"], None, "Agent resumed session")
        payload["session"] = _session_payload(session)

    build = agent_build_info()
    if build["version"]:
        payload["agent"] = build
    payload["want_inventory"] = (comp["inventory_at"] or 0) < now() - 6 * 3600
    return payload


def _owned_action(action_id: int, comp: dict) -> tuple[dict, dict]:
    a = db.one("SELECT * FROM session_actions WHERE id=?", (action_id,))
    s = db.one("SELECT * FROM sessions WHERE id=?", (a["session_id"],)) if a else None
    if not a or not s or s["computer_id"] != comp["id"]:
        raise HTTPException(404, "no such action")
    if s["status"] == "cancelled":
        raise HTTPException(409, "session cancelled")
    if s["status"] != "running":
        raise HTTPException(409, f"session is {s['status']}")
    return a, s


@router.post("/api/agent/actions/{action_id}")
def report_action(action_id: int, rep: ActionReport, comp: dict = Depends(current_computer)) -> dict:
    a, s = _owned_action(action_id, comp)
    allowed = engine.FINAL_ACTION_STATUSES | {"running"}
    if rep.status not in allowed:
        raise HTTPException(400, f"bad status {rep.status!r}")
    ts = now()
    with db.tx():
        db.run(
            "UPDATE session_actions SET status=?, detected_before=COALESCE(NULLIF(?,''),detected_before), "
            "detected_after=COALESCE(NULLIF(?,''),detected_after), message=?, "
            "started_at=COALESCE(started_at,?), finished_at=? WHERE id=?",
            (rep.status, rep.detected_before, rep.detected_after, rep.message[:2000], ts,
             ts if rep.status != "running" else None, action_id),
        )
        db.run("UPDATE sessions SET updated_at=? WHERE id=?", (ts, s["id"]))
        for line in rep.log[-2000:]:
            engine.log(db, s["id"], action_id, line, ts)
        if rep.status != "running":
            engine.log(db, s["id"], action_id, f"[{a['name']}] {rep.status}"
                       + (f": {rep.message}" if rep.message else ""), ts)
            engine.record_state(db, comp["id"], a, rep.detected_after or rep.detected_before, rep.status)
    return {"ok": True}


@router.post("/api/agent/sessions/{session_id}/finish")
def finish(session_id: int, req: FinishRequest, comp: dict = Depends(current_computer)) -> dict:
    s = db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not s or s["computer_id"] != comp["id"]:
        raise HTTPException(404, "no such session")
    if s["status"] != "running":
        return {"status": s["status"]}
    if req.message:
        engine.log(db, session_id, None, req.message)
    return {"status": engine.finish_session(db, s)}
