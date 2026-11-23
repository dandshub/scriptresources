"""NinjaOne integration tests against a fake NinjaOne API server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from server import ninjaone
from server.db import db
from server.main import app


class FakeNinja:
    def __init__(self):
        self.orgs = [{"id": 10, "name": "Ninja Acme"}, {"id": 11, "name": "Brand New Co"}]
        self.devices = [
            {"id": 100 + i, "organizationId": 10, "systemName": f"MAC{i}", "displayName": f"Acme Mac {i}",
             "nodeClass": "MAC", "offline": i == 2, "lastContact": 1700000000.5,
             "system": {"serialNumber": f"c02x{i}"}, "os": {"name": "macOS Sequoia", "releaseId": "15.1"}}
            for i in range(3)
        ]
        self.calls = []
        self.token_requests = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n).decode() if n else ""

            def do_POST(self):
                u = urlparse(self.path)
                body = self._body()
                if u.path == "/ws/oauth/token":
                    form = parse_qs(body)
                    fake.token_requests += 1
                    if form.get("client_secret") != ["s3cret"]:
                        return self._send(401, {"error": "invalid_client"})
                    return self._send(200, {"access_token": "tok", "expires_in": 3600})
                if not self._auth():
                    return
                fake.calls.append(("POST", u.path, json.loads(body or "null")))
                return self._send(204)

            def do_PATCH(self):
                if not self._auth():
                    return
                fake.calls.append(("PATCH", urlparse(self.path).path, json.loads(self._body())))
                return self._send(204)

            def do_GET(self):
                if not self._auth():
                    return
                u = urlparse(self.path)
                q = parse_qs(u.query)
                size = int(q.get("pageSize", ["500"])[0])
                after = int(q.get("after", ["0"])[0])
                if u.path == "/v2/organizations":
                    items = fake.orgs
                elif u.path == "/v2/devices-detailed":
                    assert q["df"] == ["class in (MAC)"]
                    items = fake.devices
                else:
                    return self._send(404, {"error": "nope"})
                page = [x for x in items if x["id"] > after][:size]
                return self._send(200, page)

            def _auth(self):
                if self.headers.get("Authorization") != "Bearer tok":
                    self._send(401, {"error": "unauthorized"})
                    return False
                return True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def fake():
    f = FakeNinja()
    yield f
    f.server.shutdown()


@pytest.fixture(scope="module")
def admin():
    c = TestClient(app)
    assert c.post("/api/login", json={"username": "admin", "password": "admin-password"}).status_code == 200
    return c


def test_ninjaone_flow(admin, fake):
    # Not configured yet.
    assert admin.post("/api/ninjaone/sync").status_code == 502
    assert admin.put("/api/ninjaone", json={"base_url": "ftp://x"}).status_code == 400

    r = admin.put("/api/ninjaone", json={"base_url": fake.url, "client_id": "id", "client_secret": "wrong",
                                         "script_id": "42"})
    assert r.status_code == 200 and r.json()["client_secret_set"] and "client_secret" not in r.json()
    bad = admin.post("/api/ninjaone/test")
    assert bad.status_code == 502 and "401" in bad.json()["detail"]

    admin.put("/api/ninjaone", json={"client_secret": "s3cret"})
    admin.put("/api/ninjaone", json={"client_secret": ""})  # blank keeps the secret
    assert admin.post("/api/ninjaone/test").json() == {"ok": True, "organizations": 2}

    # A tenant with the same name as a NinjaOne org links automatically.
    tid = admin.post("/api/tenants", json={"name": "Ninja Acme", "onboarding_enabled": False}).json()["id"]
    key = db.scalar("SELECT enrollment_key FROM tenants WHERE id=?", (tid,))
    # One Mac already runs the MacState agent.
    e = admin.post("/api/agent/enroll", json={"enrollment_key": key, "serial": "C02X0", "hostname": "acme0"}).json()

    fake.calls.clear()
    stats = admin.post("/api/ninjaone/sync").json()
    assert stats["organizations"] == 2 and stats["tenants_linked"] == 1 and stats["tenants_created"] == 0
    assert stats["devices"] == 3 and stats["devices_matched"] == 1 and stats["keys_pushed"] == 1
    assert ("PATCH", "/v2/organization/10/custom-fields", {"macstateEnrollmentKey": key}) in fake.calls

    comp = admin.get(f"/api/computers/{e['computer_id']}").json()
    assert comp["ninja"]["id"] == 100 and comp["ninja"]["url"].endswith("/#/deviceDashboard/100/overview")

    orgs = {o["name"]: o for o in admin.get("/api/ninjaone/orgs").json()}
    assert orgs["Ninja Acme"]["tenant_id"] == tid and orgs["Brand New Co"]["tenant_id"] is None

    # Create a tenant for the unlinked org from the UI.
    assert admin.put("/api/ninjaone/orgs/11/link", json={"create": True}).status_code == 200
    assert admin.put("/api/ninjaone/orgs/11/link", json={"create": True}).status_code == 409
    assert any(t["ninja_org_name"] == "Brand New Co" for t in admin.get("/api/tenants").json())

    # Deploy the agent to the two Macs that lack it.
    devices = admin.get("/api/ninjaone/devices").json()
    assert sum(1 for d in devices if d["computer_id"] is None) == 2
    fake.calls.clear()
    res = admin.post("/api/ninjaone/deploy", json={"all_missing": True}).json()
    assert len(res["queued"]) == 2 and not res["errors"]
    runs = [c for c in fake.calls if c[1].endswith("/script/run")]
    assert {c[1] for c in runs} == {"/v2/device/101/script/run", "/v2/device/102/script/run"}
    body = runs[0][2]
    assert body["type"] == "SCRIPT" and body["id"] == 42 and body["runAs"] == "system"
    assert body["parameters"].endswith(" " + key) and body["parameters"].startswith("http")

    # A newly enrolled Mac is linked to its NinjaOne device immediately.
    e2 = admin.post("/api/agent/enroll", json={"enrollment_key": key, "serial": "c02x1"}).json()
    assert admin.get(f"/api/computers/{e2['computer_id']}").json()["ninja"]["id"] == 101

    # Unlinking an org makes deployment report a clear error.
    admin.put("/api/ninjaone/orgs/10/link", json={"tenant_id": None})
    res = admin.post("/api/ninjaone/deploy", json={"device_ids": [102]}).json()
    assert res["errors"][0]["error"].startswith("its NinjaOne organization is not linked")


def test_paging_and_script(admin, fake):
    client = ninjaone.NinjaClient(fake.url, "id", "s3cret", "monitoring")
    fake.devices = [dict(fake.devices[0], id=1000 + i, system={"serialNumber": f"S{i}"}) for i in range(7)]
    assert len(client.paged("/v2/devices-detailed", {"df": "class in (MAC)"}, page_size=3)) == 7
    n = fake.token_requests
    client.organizations()
    assert fake.token_requests == n  # token is cached

    text = admin.get("/api/ninjaone/script").text
    assert "__SERVER__" not in text and "DEFAULT_SERVER=http" in text and "KEY_FIELD=macstateEnrollmentKey" in text


def test_sync_due(admin):
    db.set_setting("ninja.sync_interval", "3600")
    db.set_setting("ninja_state.last_attempt", "0")
    assert ninjaone.sync_due(db)
    db.set_setting("ninja.sync_interval", "0")
    assert not ninjaone.sync_due(db)
