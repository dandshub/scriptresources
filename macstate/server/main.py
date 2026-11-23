"""FastAPI app: admin web UI + REST API, and the agent endpoints."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from functools import cmp_to_key
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
import shlex

from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

from . import agent_api, config, engine, ninjaone, scheduler
from .db import db, dumps, now
from .security import hash_password, new_enrollment_key, verify_password
from .seed import bootstrap

logging.basicConfig(level=logging.INFO)
_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

bootstrap(db)
_secret = db.get_setting("session_secret")
if not _secret:
    _secret = secrets.token_hex(32)
    db.set_setting("session_secret", _secret)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sched = None
    if config.SCHEDULER:
        sched = scheduler.Scheduler(db)
        sched.start()
    yield
    if sched:
        sched.stop_event.set()


app = FastAPI(title="MacState", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=_secret, https_only=config.HTTPS_COOKIES,
                   same_site="strict", session_cookie="macstate")
app.include_router(agent_api.router)


# ---- auth ------------------------------------------------------------------

def current_user(request: Request) -> dict:
    uid = request.session.get("uid")
    user = db.one("SELECT id,username,role FROM users WHERE id=?", (uid,)) if uid else None
    if not user:
        raise HTTPException(401, "authentication required")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(req: LoginRequest, request: Request) -> dict:
    u = db.one("SELECT * FROM users WHERE username=?", (req.username,))
    if not u or not verify_password(req.password, u["pw_hash"]):
        raise HTTPException(401, "invalid credentials")
    request.session["uid"] = u["id"]
    return {"username": u["username"], "role": u["role"]}


@app.post("/api/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


# ---- users -----------------------------------------------------------------

class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8)
    role: Literal["admin", "technician"] = "technician"


class PasswordIn(BaseModel):
    password: str = Field(min_length=8)


@app.get("/api/users")
def list_users(_: dict = Depends(require_admin)) -> list[dict]:
    return db.all("SELECT id,username,role FROM users ORDER BY username")


@app.post("/api/users")
def create_user(u: UserIn, _: dict = Depends(require_admin)) -> dict:
    if db.one("SELECT id FROM users WHERE username=?", (u.username,)):
        raise HTTPException(409, "username taken")
    uid = db.run("INSERT INTO users(username,pw_hash,role) VALUES(?,?,?)",
                 (u.username, hash_password(u.password), u.role))
    return {"id": uid}


@app.delete("/api/users/{uid}")
def delete_user(uid: int, me_: dict = Depends(require_admin)) -> dict:
    if uid == me_["id"]:
        raise HTTPException(400, "cannot delete yourself")
    db.run("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@app.post("/api/users/{uid}/password")
def set_password(uid: int, p: PasswordIn, me_: dict = Depends(current_user)) -> dict:
    if uid != me_["id"] and me_["role"] != "admin":
        raise HTTPException(403, "admin only")
    db.run("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(p.password), uid))
    return {"ok": True}


# ---- generic helpers -------------------------------------------------------

def _get(table: str, id_: int) -> dict:
    row = db.one(f"SELECT * FROM {table} WHERE id=?", (id_,))
    if not row:
        raise HTTPException(404, f"{table[:-1]} not found")
    return row


def _insert(table: str, data: dict) -> int:
    data = {**data, "created_at": now()}
    cols = list(data)
    try:
        return db.run(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                      [data[c] for c in cols])
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "name already exists")
        raise


def _update(table: str, id_: int, data: dict) -> None:
    _get(table, id_)
    if not data:
        return
    try:
        db.run(f"UPDATE {table} SET {','.join(f'{c}=?' for c in data)} WHERE id=?",
               [*data.values(), id_])
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "name already exists")
        raise


def _target_label(target_type: str, target_id: Optional[int]) -> str:
    if target_type == "all":
        return "All computers"
    table = {"tenant": "tenants", "tag": "tags", "computer": "computers"}.get(target_type)
    row = db.one(f"SELECT name FROM {table} WHERE id=?", (target_id,)) if table else None
    return f"{target_type.title()}: {row['name'] if row else '?'}"


def _check_target(target_type: str, target_id: Optional[int]) -> None:
    if target_type == "all":
        return
    table = {"tenant": "tenants", "tag": "tags", "computer": "computers"}[target_type]
    if not db.one(f"SELECT id FROM {table} WHERE id=?", (target_id,)):
        raise HTTPException(400, f"{target_type} {target_id} does not exist")


# ---- tenants ---------------------------------------------------------------

class TenantIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    onboarding_enabled: bool = True
    notes: str = ""


@app.get("/api/tenants")
def list_tenants(request: Request, _: dict = Depends(current_user)) -> list[dict]:
    rows = db.all(
        "SELECT t.*, (SELECT COUNT(*) FROM computers c WHERE c.tenant_id=t.id) AS computer_count, "
        "o.name AS ninja_org_name FROM tenants t LEFT JOIN ninja_orgs o ON o.id=t.ninja_org_id ORDER BY t.name")
    base = agent_api.public_url(request)
    for r in rows:
        r["install_command"] = (f"curl -fsSL '{base}/agent/install.sh?key={r['enrollment_key']}' "
                                f"| sudo bash")
    return rows


@app.post("/api/tenants")
def create_tenant(t: TenantIn, _: dict = Depends(require_admin)) -> dict:
    return {"id": _insert("tenants", {**t.model_dump(), "enrollment_key": new_enrollment_key()})}


@app.put("/api/tenants/{tid}")
def update_tenant(tid: int, t: TenantIn, _: dict = Depends(require_admin)) -> dict:
    _update("tenants", tid, t.model_dump())
    return {"ok": True}


@app.post("/api/tenants/{tid}/rotate-key")
def rotate_key(tid: int, _: dict = Depends(require_admin)) -> dict:
    _update("tenants", tid, {"enrollment_key": new_enrollment_key()})
    return {"ok": True}


@app.delete("/api/tenants/{tid}")
def delete_tenant(tid: int, _: dict = Depends(require_admin)) -> dict:
    if db.scalar("SELECT COUNT(*) FROM computers WHERE tenant_id=?", (tid,)):
        raise HTTPException(400, "tenant still has computers; move or delete them first")
    db.run("DELETE FROM deployments WHERE target_type='tenant' AND target_id=?", (tid,))
    db.run("DELETE FROM schedules WHERE target_type='tenant' AND target_id=?", (tid,))
    db.run("DELETE FROM tenants WHERE id=?", (tid,))
    return {"ok": True}


# ---- tags ------------------------------------------------------------------

class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@app.get("/api/tags")
def list_tags(_: dict = Depends(current_user)) -> list[dict]:
    return db.all("SELECT t.*, (SELECT COUNT(*) FROM computer_tags ct WHERE ct.tag_id=t.id) AS computer_count "
                  "FROM tags t ORDER BY name")


@app.post("/api/tags")
def create_tag(t: TagIn, _: dict = Depends(current_user)) -> dict:
    try:
        return {"id": db.run("INSERT INTO tags(name) VALUES(?)", (t.name,))}
    except Exception:
        raise HTTPException(409, "tag exists")


@app.delete("/api/tags/{tid}")
def delete_tag(tid: int, _: dict = Depends(require_admin)) -> dict:
    db.run("DELETE FROM deployments WHERE target_type='tag' AND target_id=?", (tid,))
    db.run("DELETE FROM schedules WHERE target_type='tag' AND target_id=?", (tid,))
    db.run("DELETE FROM tags WHERE id=?", (tid,))
    return {"ok": True}


# ---- computers -------------------------------------------------------------

def _online(c: dict) -> bool:
    return bool(c.get("last_seen")) and c["last_seen"] >= now() - config.ONLINE_SECONDS


@app.get("/api/computers")
def list_computers(tenant_id: Optional[int] = None, _: dict = Depends(current_user)) -> list[dict]:
    sql = ("SELECT c.id,c.name,c.serial,c.model,c.os_version,c.arch,c.agent_version,c.console_user,"
           "c.last_seen,c.onboarded,c.tenant_id,c.ninja_device_id,t.name AS tenant_name,"
           "(SELECT COUNT(*) FROM computer_state s WHERE s.computer_id=c.id AND s.compliant=0) AS noncompliant,"
           "(SELECT GROUP_CONCAT(g.name, ', ') FROM computer_tags ct JOIN tags g ON g.id=ct.tag_id "
           " WHERE ct.computer_id=c.id) AS tags "
           "FROM computers c JOIN tenants t ON t.id=c.tenant_id")
    args: list = []
    if tenant_id:
        sql += " WHERE c.tenant_id=?"
        args.append(tenant_id)
    rows = db.all(sql + " ORDER BY c.name", args)
    for r in rows:
        r["online"] = _online(r)
    return rows


@app.get("/api/computers/{cid}")
def get_computer(cid: int, _: dict = Depends(current_user)) -> dict:
    c = _get("computers", cid)
    c.pop("token_hash", None)
    c["online"] = _online(c)
    c["inventory"] = json.loads(c.pop("inventory_json") or "{}")
    c["tenant_name"] = db.scalar("SELECT name FROM tenants WHERE id=?", (c["tenant_id"],))
    c["tags"] = db.all("SELECT g.id,g.name FROM computer_tags ct JOIN tags g ON g.id=ct.tag_id "
                       "WHERE ct.computer_id=? ORDER BY g.name", (cid,))
    state = {(s["item_type"], s["item_id"]): s for s in
             db.all("SELECT * FROM computer_state WHERE computer_id=?", (cid,))}
    desired = []
    for d in engine.resolve_deployments(db, c):
        table = "software" if d["item_type"] == "software" else "tasks"
        name = db.scalar(f"SELECT name FROM {table} WHERE id=?", (d["item_id"],))
        if name is None:
            continue
        st = state.get((d["item_type"], d["item_id"]))
        desired.append({
            "deployment_id": d["id"], "item_type": d["item_type"], "item_id": d["item_id"],
            "name": name, "desired": d["desired"], "version": d["version"],
            "source": _target_label(d["target_type"], d["target_id"]),
            "detected": st["detected"] if st else None,
            "status": st["status"] if st else None,
            "compliant": bool(st["compliant"]) if st else None,
            "checked_at": st["updated_at"] if st else None,
        })
    c["desired"] = sorted(desired, key=lambda x: (x["item_type"], x["name"].lower()))
    c["sessions"] = db.all("SELECT * FROM sessions WHERE computer_id=? ORDER BY id DESC LIMIT 25", (cid,))
    c["ninja"] = None
    if c.get("ninja_device_id"):
        nd = db.one("SELECT * FROM ninja_devices WHERE id=?", (c["ninja_device_id"],))
        if nd:
            c["ninja"] = {**nd, "url": ninjaone.device_url(db, nd["id"])}
    return c


class ComputerIn(BaseModel):
    name: Optional[str] = None
    tenant_id: Optional[int] = None
    tag_ids: Optional[list[int]] = None


@app.put("/api/computers/{cid}")
def update_computer(cid: int, body: ComputerIn, _: dict = Depends(current_user)) -> dict:
    _get("computers", cid)
    data = {}
    if body.name:
        data["name"] = body.name
    if body.tenant_id is not None:
        _get("tenants", body.tenant_id)
        data["tenant_id"] = body.tenant_id
    _update("computers", cid, data)
    if body.tag_ids is not None:
        with db.tx():
            db.run("DELETE FROM computer_tags WHERE computer_id=?", (cid,))
            for tid in set(body.tag_ids):
                db.run("INSERT OR IGNORE INTO computer_tags(computer_id,tag_id) "
                       "SELECT ?, id FROM tags WHERE id=?", (cid, tid))
    return {"ok": True}


@app.delete("/api/computers/{cid}")
def delete_computer(cid: int, _: dict = Depends(require_admin)) -> dict:
    db.run("DELETE FROM deployments WHERE target_type='computer' AND target_id=?", (cid,))
    db.run("DELETE FROM schedules WHERE target_type='computer' AND target_id=?", (cid,))
    db.run("DELETE FROM computers WHERE id=?", (cid,))
    return {"ok": True}


class MaintenanceIn(BaseModel):
    mode: Literal["apply", "detect"] = "apply"


class ScriptIn(BaseModel):
    script: str = Field(min_length=1)
    name: str = "Ad-hoc script"
    timeout: int = Field(1800, ge=10, le=24 * 3600)


@app.post("/api/computers/{cid}/maintenance")
def run_maintenance(cid: int, body: MaintenanceIn, user: dict = Depends(current_user)) -> dict:
    s = engine.create_session(db, _get("computers", cid), "manual", body.mode, user["username"])
    return {"session_id": s["id"]}


@app.post("/api/computers/{cid}/script")
def run_script(cid: int, body: ScriptIn, user: dict = Depends(require_admin)) -> dict:
    s = engine.create_session(db, _get("computers", cid), "adhoc", "apply", user["username"],
                              script=body.model_dump())
    return {"session_id": s["id"]}


@app.get("/api/computers/{cid}/plan")
def preview_plan(cid: int, _: dict = Depends(current_user)) -> dict:
    actions, warnings = engine.plan_for_computer(db, _get("computers", cid))
    return {"actions": [{k: a[k] for k in ("item_type", "name", "desired", "target_version")}
                        for a in actions], "warnings": warnings}


class BulkMaintenanceIn(BaseModel):
    target_type: Literal["all", "tenant", "tag", "computer"]
    target_id: Optional[int] = None
    mode: Literal["apply", "detect"] = "apply"


@app.post("/api/maintenance")
def bulk_maintenance(body: BulkMaintenanceIn, user: dict = Depends(current_user)) -> dict:
    _check_target(body.target_type, body.target_id)
    ids = [engine.create_session(db, c, "manual", body.mode, user["username"])["id"]
           for c in engine.target_computers(db, body.target_type, body.target_id)]
    return {"session_ids": ids}


# ---- software library ------------------------------------------------------

class SoftwareIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    publisher: str = ""
    description: str = ""
    detection_type: Literal["app", "pkg", "script"] = "app"
    detection_value: str = ""
    detection_script: str = ""
    installer_type: Literal["pkg", "dmg", "zip", "script"] = "pkg"
    download_url: str = ""
    install_script: str = ""
    uninstall_script: str = ""
    post_install_script: str = ""


def _validate_software(s: SoftwareIn) -> None:
    if s.detection_type in ("app", "pkg") and not s.detection_value.strip():
        raise HTTPException(400, "detection value (app path or package id) is required")
    if s.detection_type == "script" and not s.detection_script.strip():
        raise HTTPException(400, "detection script is required")
    if s.installer_type == "script" and not s.install_script.strip():
        raise HTTPException(400, "install script is required for script installers")


@app.get("/api/software")
def list_software(_: dict = Depends(current_user)) -> list[dict]:
    rows = db.all("SELECT * FROM software ORDER BY name COLLATE NOCASE")
    for r in rows:
        vs = db.all("SELECT * FROM software_versions WHERE software_id=?", (r["id"],))
        lv = engine.latest_version(vs)
        r["latest_version"] = lv["version"] if lv else ""
        r["version_count"] = len(vs)
        r["deployment_count"] = db.scalar(
            "SELECT COUNT(*) FROM deployments WHERE item_type='software' AND item_id=?", (r["id"],))
    return rows


@app.get("/api/software/{sid}")
def get_software(sid: int, _: dict = Depends(current_user)) -> dict:
    s = _get("software", sid)
    vs = db.all("SELECT * FROM software_versions WHERE software_id=?", (sid,))
    vs.sort(key=cmp_to_key(lambda a, b: engine.compare_versions(b["version"], a["version"])))
    s["versions"] = vs
    return s


@app.post("/api/software")
def create_software(s: SoftwareIn, _: dict = Depends(require_admin)) -> dict:
    _validate_software(s)
    return {"id": _insert("software", s.model_dump())}


@app.put("/api/software/{sid}")
def update_software(sid: int, s: SoftwareIn, _: dict = Depends(require_admin)) -> dict:
    _validate_software(s)
    _update("software", sid, s.model_dump())
    return {"ok": True}


@app.delete("/api/software/{sid}")
def delete_software(sid: int, _: dict = Depends(require_admin)) -> dict:
    db.run("DELETE FROM deployments WHERE item_type='software' AND item_id=?", (sid,))
    db.run("DELETE FROM computer_state WHERE item_type='software' AND item_id=?", (sid,))
    db.run("DELETE FROM software WHERE id=?", (sid,))
    return {"ok": True}


class VersionIn(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1)
    sha256: str = ""

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str) -> str:
        v = v.strip().lower()
        if v and not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be 64 hex characters")
        return v


@app.post("/api/software/{sid}/versions")
def add_version(sid: int, v: VersionIn, _: dict = Depends(require_admin)) -> dict:
    _get("software", sid)
    try:
        vid = db.run("INSERT INTO software_versions(software_id,version,url,sha256,created_at) "
                     "VALUES(?,?,?,?,?)", (sid, v.version, v.url, v.sha256, now()))
    except Exception:
        raise HTTPException(409, "version already exists")
    return {"id": vid}


@app.delete("/api/software/{sid}/versions/{vid}")
def delete_version(sid: int, vid: int, _: dict = Depends(require_admin)) -> dict:
    db.run("DELETE FROM software_versions WHERE id=? AND software_id=?", (vid, sid))
    return {"ok": True}


# ---- maintenance tasks -----------------------------------------------------

class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    test_script: str = Field(min_length=1)
    set_script: str = ""
    parameters: dict[str, str] = Field(default_factory=dict)


def _task_row(t: TaskIn) -> dict:
    d = t.model_dump()
    d["parameters_json"] = dumps(d.pop("parameters"))
    return d


@app.get("/api/tasks")
def list_tasks(_: dict = Depends(current_user)) -> list[dict]:
    rows = db.all("SELECT * FROM tasks ORDER BY name COLLATE NOCASE")
    for r in rows:
        r["parameters"] = json.loads(r.pop("parameters_json") or "{}")
        r["deployment_count"] = db.scalar(
            "SELECT COUNT(*) FROM deployments WHERE item_type='task' AND item_id=?", (r["id"],))
    return rows


@app.post("/api/tasks")
def create_task(t: TaskIn, _: dict = Depends(require_admin)) -> dict:
    return {"id": _insert("tasks", _task_row(t))}


@app.put("/api/tasks/{tid}")
def update_task(tid: int, t: TaskIn, _: dict = Depends(require_admin)) -> dict:
    _update("tasks", tid, _task_row(t))
    return {"ok": True}


@app.delete("/api/tasks/{tid}")
def delete_task(tid: int, _: dict = Depends(require_admin)) -> dict:
    db.run("DELETE FROM deployments WHERE item_type='task' AND item_id=?", (tid,))
    db.run("DELETE FROM computer_state WHERE item_type='task' AND item_id=?", (tid,))
    db.run("DELETE FROM tasks WHERE id=?", (tid,))
    return {"ok": True}


# ---- deployments -----------------------------------------------------------

class DeploymentIn(BaseModel):
    item_type: Literal["software", "task"]
    item_id: int
    target_type: Literal["all", "tenant", "tag", "computer"]
    target_id: Optional[int] = None
    desired: str
    version: str = ""
    parameters: dict[str, str] = Field(default_factory=dict)


def _deployment_row(d: DeploymentIn) -> dict:
    allowed = engine.SOFTWARE_DESIRED if d.item_type == "software" else engine.TASK_DESIRED
    if d.desired not in allowed:
        raise HTTPException(400, f"desired must be one of {', '.join(allowed)}")
    _get("software" if d.item_type == "software" else "tasks", d.item_id)
    _check_target(d.target_type, d.target_id)
    if d.desired == "version":
        if not db.one("SELECT id FROM software_versions WHERE software_id=? AND version=?",
                      (d.item_id, d.version)):
            raise HTTPException(400, f"version {d.version!r} is not in the library")
    row = d.model_dump()
    row["parameters_json"] = dumps(row.pop("parameters"))
    if d.target_type == "all":
        row["target_id"] = None
    if d.desired != "version":
        row["version"] = ""
    return row


@app.get("/api/deployments")
def list_deployments(_: dict = Depends(current_user)) -> list[dict]:
    rows = db.all("SELECT * FROM deployments ORDER BY id DESC")
    for r in rows:
        table = "software" if r["item_type"] == "software" else "tasks"
        r["item_name"] = db.scalar(f"SELECT name FROM {table} WHERE id=?", (r["item_id"],)) or "?"
        r["target_label"] = _target_label(r["target_type"], r["target_id"])
        r["parameters"] = json.loads(r.pop("parameters_json") or "{}")
    return rows


@app.post("/api/deployments")
def create_deployment(d: DeploymentIn, _: dict = Depends(current_user)) -> dict:
    return {"id": _insert("deployments", _deployment_row(d))}


@app.put("/api/deployments/{did}")
def update_deployment(did: int, d: DeploymentIn, _: dict = Depends(current_user)) -> dict:
    _update("deployments", did, _deployment_row(d))
    return {"ok": True}


@app.delete("/api/deployments/{did}")
def delete_deployment(did: int, _: dict = Depends(current_user)) -> dict:
    db.run("DELETE FROM deployments WHERE id=?", (did,))
    return {"ok": True}


# ---- schedules -------------------------------------------------------------

class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_type: Literal["all", "tenant", "tag", "computer"]
    target_id: Optional[int] = None
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: str = Field("0123456", pattern=r"^[0-6]{1,7}$")
    mode: Literal["apply", "detect"] = "apply"
    enabled: bool = True


def _schedule_row(s: ScheduleIn) -> dict:
    _check_target(s.target_type, s.target_id)
    row = s.model_dump()
    if s.target_type == "all":
        row["target_id"] = None
    # Don't fire immediately for a time that has already passed today.
    n = dt.datetime.now()
    row["last_run"] = n.strftime("%Y-%m-%d") if n.strftime("%H:%M") >= s.time else ""
    return row


@app.get("/api/schedules")
def list_schedules(_: dict = Depends(current_user)) -> list[dict]:
    rows = db.all("SELECT * FROM schedules ORDER BY time")
    for r in rows:
        r["target_label"] = _target_label(r["target_type"], r["target_id"])
    return rows


@app.post("/api/schedules")
def create_schedule(s: ScheduleIn, _: dict = Depends(require_admin)) -> dict:
    return {"id": _insert("schedules", _schedule_row(s))}


@app.put("/api/schedules/{sid}")
def update_schedule(sid: int, s: ScheduleIn, _: dict = Depends(require_admin)) -> dict:
    _update("schedules", sid, _schedule_row(s))
    return {"ok": True}


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: int, _: dict = Depends(require_admin)) -> dict:
    db.run("DELETE FROM schedules WHERE id=?", (sid,))
    return {"ok": True}


@app.post("/api/schedules/{sid}/run")
def run_schedule_now(sid: int, user: dict = Depends(current_user)) -> dict:
    return {"queued": scheduler.run_schedule(db, _get("schedules", sid), created_by=user["username"])}


# ---- sessions --------------------------------------------------------------

@app.get("/api/sessions")
def list_sessions(computer_id: Optional[int] = None, status: Optional[str] = None,
                  limit: int = 100, _: dict = Depends(current_user)) -> list[dict]:
    sql = ("SELECT s.*, c.name AS computer_name, t.name AS tenant_name, "
           "(SELECT COUNT(*) FROM session_actions a WHERE a.session_id=s.id) AS action_count, "
           "(SELECT COUNT(*) FROM session_actions a WHERE a.session_id=s.id AND a.status='failed') AS failed_count "
           "FROM sessions s JOIN computers c ON c.id=s.computer_id JOIN tenants t ON t.id=c.tenant_id")
    where, args = [], []
    if computer_id:
        where.append("s.computer_id=?")
        args.append(computer_id)
    if status:
        where.append("s.status=?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.all(sql + " ORDER BY s.id DESC LIMIT ?", [*args, min(max(limit, 1), 500)])


@app.get("/api/sessions/{sid}")
def get_session(sid: int, _: dict = Depends(current_user)) -> dict:
    s = engine.session_detail(db, sid)
    if not s:
        raise HTTPException(404, "session not found")
    return s


@app.post("/api/sessions/{sid}/cancel")
def cancel_session(sid: int, user: dict = Depends(current_user)) -> dict:
    s = _get("sessions", sid)
    if s["status"] not in ("pending", "running"):
        raise HTTPException(400, f"session is already {s['status']}")
    with db.tx():
        db.run("UPDATE sessions SET status='cancelled', finished_at=?, updated_at=? WHERE id=?",
               (now(), now(), sid))
        db.run("UPDATE session_actions SET status='cancelled' WHERE session_id=? "
               "AND status IN ('pending','running')", (sid,))
        engine.log(db, sid, None, f"Session cancelled by {user['username']}")
    return {"ok": True}


# ---- NinjaOne --------------------------------------------------------------

def _ninja(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ninjaone.NinjaError as e:
        raise HTTPException(502, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class NinjaSettingsIn(BaseModel):
    base_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scope: Optional[str] = None
    device_classes: Optional[str] = None
    custom_field: Optional[str] = None
    push_keys: Optional[bool] = None
    auto_create_tenants: Optional[bool] = None
    script_id: Optional[str] = None
    run_as: Optional[str] = None
    sync_interval: Optional[str] = None


@app.get("/api/ninjaone")
def ninja_settings(_: dict = Depends(require_admin)) -> dict:
    return ninjaone.public_settings(db)


@app.put("/api/ninjaone")
def ninja_save(body: NinjaSettingsIn, _: dict = Depends(require_admin)) -> dict:
    _ninja(ninjaone.save_settings, db, body.model_dump(exclude_none=True))
    return ninjaone.public_settings(db)


@app.post("/api/ninjaone/test")
def ninja_test(_: dict = Depends(require_admin)) -> dict:
    client = _ninja(ninjaone.NinjaClient.from_db, db)
    orgs = _ninja(client.organizations)
    return {"ok": True, "organizations": len(orgs)}


@app.post("/api/ninjaone/sync")
def ninja_sync(_: dict = Depends(current_user)) -> dict:
    return _ninja(ninjaone.sync, db)


@app.get("/api/ninjaone/orgs")
def ninja_orgs(_: dict = Depends(current_user)) -> list[dict]:
    return db.all(
        "SELECT o.*, t.id AS tenant_id, t.name AS tenant_name, "
        "(SELECT COUNT(*) FROM ninja_devices d WHERE d.organization_id=o.id) AS device_count, "
        "(SELECT COUNT(*) FROM ninja_devices d WHERE d.organization_id=o.id AND d.computer_id IS NOT NULL) AS agent_count "
        "FROM ninja_orgs o LEFT JOIN tenants t ON t.ninja_org_id=o.id ORDER BY o.name COLLATE NOCASE")


class NinjaLinkIn(BaseModel):
    tenant_id: Optional[int] = None   # null unlinks
    create: bool = False              # create a tenant named after the org


@app.put("/api/ninjaone/orgs/{org_id}/link")
def ninja_link(org_id: int, body: NinjaLinkIn, _: dict = Depends(require_admin)) -> dict:
    org = db.one("SELECT * FROM ninja_orgs WHERE id=?", (org_id,))
    if not org:
        raise HTTPException(404, "unknown NinjaOne organization (sync first)")
    with db.tx():
        db.run("UPDATE tenants SET ninja_org_id=NULL WHERE ninja_org_id=?", (org_id,))
        if body.create:
            if db.one("SELECT id FROM tenants WHERE lower(name)=lower(?)", (org["name"],)):
                raise HTTPException(409, f"a tenant named {org['name']!r} already exists; link it instead")
            db.run("INSERT INTO tenants(name,enrollment_key,ninja_org_id,created_at) VALUES(?,?,?,?)",
                   (org["name"], new_enrollment_key(), org_id, now()))
        elif body.tenant_id is not None:
            _get("tenants", body.tenant_id)
            db.run("UPDATE tenants SET ninja_org_id=? WHERE id=?", (org_id, body.tenant_id))
    return {"ok": True}


@app.get("/api/ninjaone/devices")
def ninja_devices(_: dict = Depends(current_user)) -> list[dict]:
    rows = db.all(
        "SELECT d.*, o.name AS org_name, t.name AS tenant_name, c.name AS computer_name, c.last_seen AS agent_last_seen "
        "FROM ninja_devices d LEFT JOIN ninja_orgs o ON o.id=d.organization_id "
        "LEFT JOIN tenants t ON t.ninja_org_id=d.organization_id "
        "LEFT JOIN computers c ON c.id=d.computer_id ORDER BY d.name COLLATE NOCASE")
    for r in rows:
        r["url"] = ninjaone.device_url(db, r["id"])
    return rows


class NinjaDeployIn(BaseModel):
    device_ids: list[int] = Field(default_factory=list)
    all_missing: bool = False


@app.post("/api/ninjaone/deploy")
def ninja_deploy(body: NinjaDeployIn, request: Request, _: dict = Depends(require_admin)) -> dict:
    ids = list(body.device_ids)
    if body.all_missing:
        ids += [r["id"] for r in db.all(
            "SELECT d.id FROM ninja_devices d JOIN tenants t ON t.ninja_org_id=d.organization_id "
            "WHERE d.computer_id IS NULL")]
    if not ids:
        raise HTTPException(400, "no devices selected")
    return _ninja(ninjaone.deploy_agent, db, sorted(set(ids)), agent_api.public_url(request))


@app.get("/api/ninjaone/script", response_class=PlainTextResponse)
def ninja_script(request: Request, _: dict = Depends(require_admin)) -> str:
    path = os.path.join(os.path.dirname(_STATIC), "deploy", "ninjaone-install-macstate.sh")
    with open(path) as fh:
        text = fh.read()
    field = ninjaone.get_settings(db)["custom_field"] or "macstateEnrollmentKey"
    return (text.replace("__SERVER__", shlex.quote(agent_api.public_url(request)))
                .replace("__FIELD__", shlex.quote(field)))


# ---- dashboard -------------------------------------------------------------

@app.get("/api/dashboard")
def dashboard(_: dict = Depends(current_user)) -> dict:
    cutoff = now() - config.ONLINE_SECONDS
    day = now() - 86400
    return {
        "computers": db.scalar("SELECT COUNT(*) FROM computers"),
        "online": db.scalar("SELECT COUNT(*) FROM computers WHERE last_seen>=?", (cutoff,)),
        "tenants": db.scalar("SELECT COUNT(*) FROM tenants"),
        "software": db.scalar("SELECT COUNT(*) FROM software"),
        "deployments": db.scalar("SELECT COUNT(*) FROM deployments"),
        "noncompliant_computers": db.scalar(
            "SELECT COUNT(DISTINCT computer_id) FROM computer_state WHERE compliant=0"),
        "sessions_24h": {r["status"]: r["n"] for r in db.all(
            "SELECT status, COUNT(*) AS n FROM sessions WHERE created_at>=? GROUP BY status", (day,))},
        "active_sessions": db.scalar("SELECT COUNT(*) FROM sessions WHERE status IN ('pending','running')"),
        "noncompliant_items": db.all(
            "SELECT s.*, c.name AS computer_name FROM computer_state s JOIN computers c ON c.id=s.computer_id "
            "WHERE s.compliant=0 ORDER BY s.updated_at DESC LIMIT 20"),
        "recent_sessions": db.all(
            "SELECT s.id,s.kind,s.mode,s.status,s.created_at,s.finished_at,c.name AS computer_name "
            "FROM sessions s JOIN computers c ON c.id=s.computer_id ORDER BY s.id DESC LIMIT 10"),
        "agent": agent_api.agent_build_info(),
    }


# ---- static UI -------------------------------------------------------------

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(_STATIC, "index.html"))
