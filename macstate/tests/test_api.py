"""API + engine tests using FastAPI's TestClient (no Mac needed).

Run:  python -m pytest tests
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from server import engine, scheduler
from server.db import db
from server.main import app


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "admin", "password": "admin-password"})
    assert r.status_code == 200
    return c


def enroll(client, key, serial, hostname=None):
    r = client.post("/api/agent/enroll", json={
        "enrollment_key": key, "serial": serial, "hostname": hostname or serial,
        "os_version": "macOS 15.1", "arch": "arm64"})
    assert r.status_code == 200, r.text
    return r.json()


def agent(token):
    return {"Authorization": f"Bearer {token}"}


def test_compare_versions():
    cv = engine.compare_versions
    assert cv("1.2", "1.10") == -1
    assert cv("1.0", "1.0.0") == 0
    assert cv("1.0b1", "1.0") == -1
    assert cv("2", "1.99") == 1
    assert cv("120.0.6099.109", "120.0.6099.71") == 1


def test_requires_login():
    assert TestClient(app).get("/api/computers").status_code == 401
    bad = TestClient(app).post("/api/login", json={"username": "admin", "password": "nope"})
    assert bad.status_code == 401


def test_full_flow(admin):
    tenant = admin.post("/api/tenants", json={"name": "Acme", "onboarding_enabled": True}).json()
    t = next(x for x in admin.get("/api/tenants").json() if x["id"] == tenant["id"])
    assert "install.sh?key=" in t["install_command"]
    assert admin.get(f"/agent/install.sh?key={t['enrollment_key']}").status_code == 200
    assert admin.get("/agent/install.sh?key=wrong").status_code == 404

    # Bad key rejected.
    assert admin.post("/api/agent/enroll", json={"enrollment_key": "nope"}).status_code == 403

    sw = admin.post("/api/software", json={
        "name": "Widget", "detection_type": "app", "detection_value": "/Applications/Widget.app",
        "installer_type": "pkg"}).json()
    admin.post(f"/api/software/{sw['id']}/versions", json={"version": "1.2.0", "url": "https://x/1.2.pkg"})
    admin.post(f"/api/software/{sw['id']}/versions", json={"version": "1.10.0", "url": "https://x/1.10.pkg",
                                                           "sha256": "A" * 64})
    bad = admin.post(f"/api/software/{sw['id']}/versions", json={"version": "2", "url": "u", "sha256": "xyz"})
    assert bad.status_code == 422

    task = admin.post("/api/tasks", json={"name": "Firewall X", "test_script": "exit 1",
                                          "set_script": "true", "parameters": {"A": "1"}}).json()
    admin.post("/api/deployments", json={"item_type": "software", "item_id": sw["id"],
                                         "target_type": "tenant", "target_id": tenant["id"], "desired": "latest"})
    admin.post("/api/deployments", json={"item_type": "task", "item_id": task["id"], "target_type": "all",
                                         "desired": "enforce", "parameters": {"A": "2"}})

    # Enrollment queues an onboarding session.
    e = enroll(admin, t["enrollment_key"], "SER1", "Alice's Mac")
    r = admin.post("/api/agent/checkin", json={"hostname": "Alice's Mac"}, headers=agent(e["token"]))
    assert r.status_code == 200
    sess = r.json()["session"]
    assert sess and sess["kind"] == "onboarding"
    names = [a["name"] for a in sess["actions"]]
    assert names == ["Widget", "Firewall X"]  # software before tasks
    w = sess["actions"][0]
    assert w["target_version"] == "1.10.0" and w["installer"]["sha256"] == "a" * 64
    assert sess["actions"][1]["parameters"] == {"A": "2"}  # deployment override wins

    # Agent reports results.
    hdr = agent(e["token"])
    assert admin.post(f"/api/agent/actions/{w['id']}", headers=hdr, json={
        "status": "installed", "detected_before": "not installed", "detected_after": "1.10.0",
        "log": ["line one"]}).status_code == 200
    admin.post(f"/api/agent/actions/{sess['actions'][1]['id']}", headers=hdr,
               json={"status": "failed", "message": "boom"})
    fin = admin.post(f"/api/agent/sessions/{sess['id']}/finish", headers=hdr, json={}).json()
    assert fin["status"] == "failed"

    comp = admin.get(f"/api/computers/{e['computer_id']}").json()
    state = {d["name"]: d for d in comp["desired"]}
    assert state["Widget"]["compliant"] is True and state["Widget"]["detected"] == "1.10.0"
    assert state["Firewall X"]["compliant"] is False
    assert comp["onboarded"] == 0

    detail = admin.get(f"/api/sessions/{sess['id']}").json()
    assert any("line one" in l["line"] for l in detail["logs"])

    # Another computer's token can't report on this session.
    e2 = enroll(admin, t["enrollment_key"], "SER2")
    assert admin.post(f"/api/agent/actions/{w['id']}", headers=agent(e2["token"]),
                      json={"status": "failed"}).status_code == 404

    # Re-enrolling the same serial reuses the record and rotates the token.
    e1b = enroll(admin, t["enrollment_key"], "SER1")
    assert e1b["computer_id"] == e["computer_id"]
    assert admin.post("/api/agent/checkin", json={}, headers=agent(e["token"])).status_code == 401


def test_precedence_and_ignore(admin):
    t = admin.post("/api/tenants", json={"name": "Prec"}).json()
    key = db.scalar("SELECT enrollment_key FROM tenants WHERE id=?", (t["id"],))
    admin.put(f"/api/tenants/{t['id']}", json={"name": "Prec", "onboarding_enabled": False})
    e = enroll(admin, key, "PREC1")
    cid = e["computer_id"]
    sw = admin.post("/api/software", json={"name": "Thing", "detection_type": "pkg",
                                           "detection_value": "com.thing", "installer_type": "pkg",
                                           "download_url": "https://x/thing.pkg"}).json()
    tag = admin.post("/api/tags", json={"name": "lab"}).json()
    admin.put(f"/api/computers/{cid}", json={"tag_ids": [tag["id"]]})

    admin.post("/api/deployments", json={"item_type": "software", "item_id": sw["id"], "target_type": "all",
                                         "desired": "installed"})
    plan = lambda: {a["name"]: a for a in admin.get(f"/api/computers/{cid}/plan").json()["actions"]}
    assert plan()["Thing"]["desired"] == "installed"

    admin.post("/api/deployments", json={"item_type": "software", "item_id": sw["id"], "target_type": "tag",
                                         "target_id": tag["id"], "desired": "uninstalled"})
    assert plan()["Thing"]["desired"] == "uninstalled"

    # Tenant-level is less specific than tag, so tag still wins.
    admin.post("/api/deployments", json={"item_type": "software", "item_id": sw["id"], "target_type": "tenant",
                                         "target_id": t["id"], "desired": "latest"})
    assert plan()["Thing"]["desired"] == "uninstalled"

    admin.post("/api/deployments", json={"item_type": "software", "item_id": sw["id"], "target_type": "computer",
                                         "target_id": cid, "desired": "ignore"})
    assert "Thing" not in plan()

    # "latest" with only a rolling URL degrades to "installed".
    sw2 = admin.post("/api/software", json={"name": "Rolling", "detection_type": "app",
                                            "detection_value": "Rolling", "installer_type": "dmg",
                                            "download_url": "https://x/r.dmg"}).json()
    admin.post("/api/deployments", json={"item_type": "software", "item_id": sw2["id"], "target_type": "computer",
                                         "target_id": cid, "desired": "latest"})
    a = plan()["Rolling"]
    assert a["desired"] == "installed" and a["target_version"] == ""

    # Specific version must exist.
    bad = admin.post("/api/deployments", json={"item_type": "software", "item_id": sw2["id"],
                                               "target_type": "all", "desired": "version", "version": "9"})
    assert bad.status_code == 400


def test_manual_session_dedupe_and_cancel(admin):
    t = admin.post("/api/tenants", json={"name": "Dedupe", "onboarding_enabled": False}).json()
    key = db.scalar("SELECT enrollment_key FROM tenants WHERE id=?", (t["id"],))
    e = enroll(admin, key, "DD1")
    s1 = admin.post(f"/api/computers/{e['computer_id']}/maintenance", json={"mode": "detect"}).json()
    s2 = admin.post(f"/api/computers/{e['computer_id']}/maintenance", json={"mode": "apply"}).json()
    assert s1["session_id"] == s2["session_id"]

    r = admin.post("/api/agent/checkin", json={}, headers=agent(e["token"])).json()
    assert r["session"]["id"] == s1["session_id"]
    assert admin.post(f"/api/sessions/{s1['session_id']}/cancel").status_code == 200
    if r["session"]["actions"]:
        aid = r["session"]["actions"][0]["id"]
        assert admin.post(f"/api/agent/actions/{aid}", headers=agent(e["token"]),
                          json={"status": "compliant"}).status_code == 409
    # Nothing left to hand out.
    assert admin.post("/api/agent/checkin", json={}, headers=agent(e["token"])).json()["session"] is None


def test_scheduler_tick(admin):
    t = admin.post("/api/tenants", json={"name": "Sched", "onboarding_enabled": False}).json()
    key = db.scalar("SELECT enrollment_key FROM tenants WHERE id=?", (t["id"],))
    e = enroll(admin, key, "SCH1")
    sid = admin.post("/api/schedules", json={"name": "Nightly", "target_type": "tenant", "target_id": t["id"],
                                             "time": "23:59", "days": "0123456"}).json()["id"]
    # Force it to look not-yet-run, then tick at 23:59.
    db.run("UPDATE schedules SET last_run='' WHERE id=?", (sid,))
    when = dt.datetime.now().replace(hour=23, minute=59)
    scheduler.tick(db, when)
    sessions = admin.get(f"/api/sessions?computer_id={e['computer_id']}").json()
    assert len(sessions) == 1 and sessions[0]["kind"] == "scheduled"
    scheduler.tick(db, when)  # same day: no second run
    assert len(admin.get(f"/api/sessions?computer_id={e['computer_id']}").json()) == 1


def test_technician_permissions(admin):
    admin.post("/api/users", json={"username": "tech", "password": "tech-password", "role": "technician"})
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "tech", "password": "tech-password"}).status_code == 200
    assert c.get("/api/computers").status_code == 200
    assert c.post("/api/software", json={"name": "Nope", "detection_value": "x"}).status_code == 403
    assert c.post("/api/computers/1/script", json={"script": "id"}).status_code == 403


def test_dashboard(admin):
    d = admin.get("/api/dashboard").json()
    assert d["computers"] >= 1 and "sessions_24h" in d
    assert admin.get("/").status_code == 200
