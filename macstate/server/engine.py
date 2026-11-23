"""Desired-state engine: resolves which deployments apply to a computer and
turns them into a maintenance session the agent executes.

Precedence: when several deployments target the same software/task, the most
specific target wins (computer > tag > tenant > all); ties go to the newest
deployment. A winning deployment with desired == "ignore" removes the item.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .db import DB, dumps, now

SPECIFICITY = {"all": 1, "tenant": 2, "tag": 3, "computer": 4}
SOFTWARE_DESIRED = ("installed", "latest", "version", "uninstalled", "ignore")
TASK_DESIRED = ("enforce", "monitor", "ignore")
COMPLIANT_STATUSES = {"compliant", "installed", "updated", "uninstalled", "remediated", "succeeded"}
FINAL_ACTION_STATUSES = COMPLIANT_STATUSES | {"noncompliant", "failed", "skipped", "cancelled"}

_TOKEN = re.compile(r"\d+|[A-Za-z]+")


# ---- versions --------------------------------------------------------------

def compare_versions(a: str, b: str) -> int:
    """Compare dotted version strings. Returns -1, 0 or 1.

    Numeric tokens compare numerically; a trailing alphabetic token marks a
    pre-release, so 1.0b1 < 1.0 < 1.0.1. Mirrors agent/version.go."""
    ta, tb = _TOKEN.findall(a or ""), _TOKEN.findall(b or "")
    for i in range(max(len(ta), len(tb))):
        x = ta[i] if i < len(ta) else None
        y = tb[i] if i < len(tb) else None
        if x is None:
            if y.isdigit():
                x = "0"
            else:
                return 1
        if y is None:
            if x.isdigit():
                y = "0"
            else:
                return -1
        xd, yd = x.isdigit(), y.isdigit()
        if xd and yd:
            xi, yi = int(x), int(y)
            if xi != yi:
                return -1 if xi < yi else 1
        elif xd != yd:
            return 1 if xd else -1
        else:
            xl, yl = x.lower(), y.lower()
            if xl != yl:
                return -1 if xl < yl else 1
    return 0


def latest_version(versions: list[dict]) -> dict | None:
    best = None
    for v in versions:
        if best is None or compare_versions(v["version"], best["version"]) > 0:
            best = v
    return best


# ---- targeting -------------------------------------------------------------

def computer_tag_ids(db: DB, computer_id: int) -> set[int]:
    rows = db.all("SELECT tag_id FROM computer_tags WHERE computer_id=?", (computer_id,))
    return {r["tag_id"] for r in rows}


def target_matches(dep: dict, computer: dict, tag_ids: set[int]) -> bool:
    t = dep["target_type"]
    if t == "all":
        return True
    if t == "tenant":
        return dep["target_id"] == computer["tenant_id"]
    if t == "tag":
        return dep["target_id"] in tag_ids
    if t == "computer":
        return dep["target_id"] == computer["id"]
    return False


def target_computers(db: DB, target_type: str, target_id: int | None) -> list[dict]:
    if target_type == "all":
        return db.all("SELECT * FROM computers ORDER BY name")
    if target_type == "tenant":
        return db.all("SELECT * FROM computers WHERE tenant_id=? ORDER BY name", (target_id,))
    if target_type == "tag":
        return db.all(
            "SELECT c.* FROM computers c JOIN computer_tags ct ON ct.computer_id=c.id "
            "WHERE ct.tag_id=? ORDER BY c.name",
            (target_id,),
        )
    if target_type == "computer":
        return db.all("SELECT * FROM computers WHERE id=?", (target_id,))
    return []


def resolve_deployments(db: DB, computer: dict) -> list[dict]:
    """Winning deployment per item for this computer, ignores removed."""
    tag_ids = computer_tag_ids(db, computer["id"])
    winners: dict[tuple[str, int], dict] = {}
    for dep in db.all("SELECT * FROM deployments"):
        if not target_matches(dep, computer, tag_ids):
            continue
        key = (dep["item_type"], dep["item_id"])
        rank = (SPECIFICITY.get(dep["target_type"], 0), dep["id"])
        cur = winners.get(key)
        if cur is None or rank > cur["_rank"]:
            winners[key] = {**dep, "_rank": rank}
    return [d for d in winners.values() if d["desired"] != "ignore"]


# ---- action specs ----------------------------------------------------------

def software_spec(db: DB, sw: dict, desired: str, version: str) -> tuple[dict, str]:
    """Build the agent-facing spec for a software deployment.
    Returns (spec, target_version)."""
    versions = db.all("SELECT * FROM software_versions WHERE software_id=?", (sw["id"],))
    chosen: dict | None = None
    if desired == "version":
        chosen = next((v for v in versions if v["version"] == version), None)
        if chosen is None:
            raise ValueError(f"{sw['name']}: version {version!r} is not in the library")
    elif desired in ("installed", "latest"):
        chosen = latest_version(versions)

    target_version = chosen["version"] if chosen else ""
    if desired == "latest" and not chosen:
        # Only a rolling URL exists: there is no version to compare against.
        desired = "installed"
    url = chosen["url"] if chosen else sw["download_url"]
    spec = {
        "desired": desired,
        "target_version": target_version,
        "detection": {
            "type": sw["detection_type"],
            "value": sw["detection_value"],
            "script": sw["detection_script"],
        },
        "installer": {
            "type": sw["installer_type"],
            "url": url,
            "sha256": (chosen or {}).get("sha256", ""),
            "install_script": sw["install_script"],
            "uninstall_script": sw["uninstall_script"],
            "post_install_script": sw["post_install_script"],
        },
    }
    return spec, target_version


def task_spec(task: dict, desired: str, overrides: dict) -> dict:
    params = json.loads(task["parameters_json"] or "{}")
    params.update(overrides or {})
    return {
        "desired": desired,
        "test_script": task["test_script"],
        "set_script": task["set_script"],
        "parameters": {k: str(v) for k, v in params.items()},
    }


def plan_for_computer(db: DB, computer: dict) -> tuple[list[dict], list[str]]:
    """Ordered action list for a maintenance session plus planning warnings.

    Order: uninstalls, then installs/updates (by name), then tasks."""
    actions: list[dict] = []
    warnings: list[str] = []
    for dep in resolve_deployments(db, computer):
        overrides = json.loads(dep["parameters_json"] or "{}")
        if dep["item_type"] == "software":
            sw = db.one("SELECT * FROM software WHERE id=?", (dep["item_id"],))
            if not sw:
                continue
            try:
                spec, tv = software_spec(db, sw, dep["desired"], dep["version"])
            except ValueError as e:
                warnings.append(str(e))
                continue
            order = 0 if dep["desired"] == "uninstalled" else 1
            actions.append({
                "order": (order, sw["name"].lower()), "item_type": "software",
                "item_id": sw["id"], "name": sw["name"], "desired": spec["desired"],
                "target_version": tv, "spec": spec,
            })
        elif dep["item_type"] == "task":
            task = db.one("SELECT * FROM tasks WHERE id=?", (dep["item_id"],))
            if not task:
                continue
            actions.append({
                "order": (2, task["name"].lower()), "item_type": "task",
                "item_id": task["id"], "name": task["name"], "desired": dep["desired"],
                "target_version": "", "spec": task_spec(task, dep["desired"], overrides),
            })
    actions.sort(key=lambda a: a["order"])
    return actions, warnings


# ---- sessions --------------------------------------------------------------

def open_session(db: DB, computer_id: int) -> dict | None:
    return db.one(
        "SELECT * FROM sessions WHERE computer_id=? AND status IN ('pending','running') "
        "ORDER BY id LIMIT 1",
        (computer_id,),
    )


def create_session(db: DB, computer: dict, kind: str, mode: str, created_by: str = "",
                   script: dict | None = None) -> dict:
    """Create a maintenance session (or an ad-hoc script session).

    Maintenance sessions are de-duplicated: if the computer already has one
    queued or running, that session is returned instead."""
    if script is None:
        existing = open_session(db, computer["id"])
        if existing and existing["kind"] != "adhoc":
            return existing
        actions, warnings = plan_for_computer(db, computer)
    else:
        actions = [{
            "item_type": "script", "item_id": None, "name": script.get("name") or "Ad-hoc script",
            "desired": "run", "target_version": "",
            "spec": {"script": script["script"], "timeout": int(script.get("timeout") or 1800)},
        }]
        warnings = []

    with db.tx():
        sid = db.run(
            "INSERT INTO sessions(computer_id,kind,mode,status,created_by,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (computer["id"], kind, mode, "pending", created_by, now()),
        )
        for i, a in enumerate(actions):
            db.run(
                "INSERT INTO session_actions(session_id,seq,item_type,item_id,name,desired,"
                "target_version,spec_json) VALUES(?,?,?,?,?,?,?,?)",
                (sid, i, a["item_type"], a["item_id"], a["name"], a["desired"],
                 a["target_version"], dumps(a["spec"])),
            )
        log(db, sid, None, f"Session queued ({kind}, {mode}) with {len(actions)} action(s)"
            + (f" by {created_by}" if created_by else ""))
        for w in warnings:
            log(db, sid, None, f"WARNING: {w}")
    return db.one("SELECT * FROM sessions WHERE id=?", (sid,))


def log(db: DB, session_id: int, action_id: int | None, line: str, ts: int | None = None) -> None:
    db.run(
        "INSERT INTO session_logs(session_id,action_id,ts,line) VALUES(?,?,?,?)",
        (session_id, action_id, ts or now(), line[:8000]),
    )


def finish_session(db: DB, session: dict) -> str:
    """Close a session; overall status derives from its actions."""
    acts = db.all("SELECT status FROM session_actions WHERE session_id=?", (session["id"],))
    with db.tx():
        db.run("UPDATE session_actions SET status='skipped' WHERE session_id=? "
               "AND status IN ('pending','running')", (session["id"],))
        # Non-compliance (detect mode, monitor-only tasks) is reported per item;
        # only an action that errored fails the session.
        status = "failed" if any(a["status"] == "failed" for a in acts) else "succeeded"
        db.run("UPDATE sessions SET status=?, finished_at=?, updated_at=? WHERE id=?",
               (status, now(), now(), session["id"]))
        if session["kind"] == "onboarding" and status == "succeeded":
            db.run("UPDATE computers SET onboarded=1 WHERE id=?", (session["computer_id"],))
        log(db, session["id"], None, f"Session {status}")
    return status


def record_state(db: DB, computer_id: int, action: dict, detected: str, status: str) -> None:
    if action["item_type"] not in ("software", "task"):
        return
    db.run(
        "INSERT INTO computer_state(computer_id,item_type,item_id,name,detected,status,compliant,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(computer_id,item_type,item_id) DO UPDATE SET "
        "name=excluded.name, detected=excluded.detected, status=excluded.status, "
        "compliant=excluded.compliant, updated_at=excluded.updated_at",
        (computer_id, action["item_type"], action["item_id"], action["name"], detected,
         status, int(status in COMPLIANT_STATUSES), now()),
    )


def session_detail(db: DB, session_id: int) -> dict[str, Any] | None:
    s = db.one(
        "SELECT s.*, c.name AS computer_name FROM sessions s JOIN computers c ON c.id=s.computer_id "
        "WHERE s.id=?", (session_id,))
    if not s:
        return None
    s["actions"] = db.all(
        "SELECT id,seq,item_type,item_id,name,desired,target_version,status,detected_before,"
        "detected_after,message,started_at,finished_at FROM session_actions WHERE session_id=? ORDER BY seq",
        (session_id,))
    s["logs"] = db.all("SELECT action_id,ts,line FROM session_logs WHERE session_id=? ORDER BY id",
                       (session_id,))
    return s
