"""End-to-end: run the real Go agent (built for the host OS) against a live
server. Uses script-based detection/installers so it runs on Linux CI; the
macOS-specific handlers (pkg/dmg/zip) need a real Mac.

Skipped when Go is not installed.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    agent_bin = tmp / "macstate-agent"
    subprocess.run(["go", "build", "-o", str(agent_bin), "."], cwd=os.path.join(ROOT, "agent"), check=True)
    port = free_port()
    server_env = {**os.environ, "MACSTATE_DB": str(tmp / "db.sqlite"), "MACSTATE_AGENT_DIR": str(tmp / "build"),
                  "MACSTATE_ADMIN_PASSWORD": "admin-password", "MACSTATE_SCHEDULER": "0",
                  "MACSTATE_SEED": "0", "MACSTATE_POLL_SECONDS": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--port", str(port)],
                            cwd=ROOT, env=server_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/")
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    client = httpx.Client(base_url=base)
    assert client.post("/api/login", json={"username": "admin", "password": "admin-password"}).status_code == 200
    yield {"tmp": tmp, "agent": agent_bin, "base": base, "client": client}
    proc.terminate()
    proc.wait(5)


def run_agent(env, *args):
    e = {**os.environ, "MACSTATE_AGENT_HOME": str(env["tmp"] / "agent-home")}
    return subprocess.run([str(env["agent"]), *args], env=e, capture_output=True, text=True, timeout=120)


def test_agent_end_to_end(env):
    c, tmp = env["client"], env["tmp"]
    marker = tmp / "installed-version"
    tenant = c.get("/api/tenants").json()[0]

    sw = c.post("/api/software", json={
        "name": "FakeApp", "detection_type": "script", "installer_type": "script",
        "detection_script": f'[ -f "{marker}" ] && cat "{marker}"\n',
        "install_script": f'#!/bin/sh\necho "installing $MS_TARGET_VERSION"\nprintf "%s" "$MS_TARGET_VERSION" > "{marker}"\n',
        "uninstall_script": f'rm -f "{marker}"\n',
    }).json()
    assert "id" in sw, sw
    # Script installers download the version's URL first (exposed to the
    # script as MS_INSTALLER_PATH); point it at a file the server itself serves.
    for v in ("1.0", "2.0"):
        r = c.post(f"/api/software/{sw['id']}/versions",
                   json={"version": v, "url": env["base"] + "/static/style.css"})
        assert r.status_code == 200, r.text

    flag = tmp / "configured"
    task = c.post("/api/tasks", json={
        "name": "Config flag", "test_script": f'[ "$(cat "{flag}" 2>/dev/null)" = "$WANT" ]\n',
        "set_script": f'printf "%s" "$WANT" > "{flag}"\n', "parameters": {"WANT": "default"}}).json()

    c.post("/api/deployments", json={"item_type": "software", "item_id": sw["id"], "target_type": "all",
                                     "desired": "version", "version": "1.0"})
    c.post("/api/deployments", json={"item_type": "task", "item_id": task["id"], "target_type": "all",
                                     "desired": "enforce", "parameters": {"WANT": "yes"}})

    r = run_agent(env, "enroll", "--server", env["base"], "--key", tenant["enrollment_key"])
    assert r.returncode == 0, r.stderr
    cid = json.loads((tmp / "agent-home" / "agent.json").read_text())["computer_id"]

    # Onboarding session runs on first check-in.
    r = run_agent(env, "once")
    assert r.returncode == 0, r.stderr + r.stdout
    assert marker.read_text() == "1.0"
    assert flag.read_text() == "yes"
    s = c.get(f"/api/sessions?computer_id={cid}").json()[0]
    assert s["kind"] == "onboarding" and s["status"] == "succeeded", s
    detail = c.get(f"/api/sessions/{s['id']}").json()
    statuses = {a["name"]: a["status"] for a in detail["actions"]}
    assert statuses == {"FakeApp": "installed", "Config flag": "remediated"}
    assert any("installing 1.0" in l["line"] for l in detail["logs"])
    comp = c.get(f"/api/computers/{cid}").json()
    assert comp["onboarded"] == 1 and comp["inventory"]  # inventory sent on first check-in

    # Move the deployment to "latest" -> upgrade to 2.0.
    dep = next(d for d in c.get("/api/deployments").json() if d["item_type"] == "software")
    c.put(f"/api/deployments/{dep['id']}", json={"item_type": "software", "item_id": sw["id"],
                                                 "target_type": "all", "desired": "latest"})
    c.post(f"/api/computers/{cid}/maintenance", json={"mode": "detect"})
    assert run_agent(env, "once").returncode == 0
    s = c.get(f"/api/sessions?computer_id={cid}").json()[0]
    acts = {a["name"]: a for a in c.get(f"/api/sessions/{s['id']}").json()["actions"]}
    assert acts["FakeApp"]["status"] == "noncompliant" and marker.read_text() == "1.0"
    assert acts["Config flag"]["status"] == "compliant"

    c.post(f"/api/computers/{cid}/maintenance", json={"mode": "apply"})
    assert run_agent(env, "once").returncode == 0
    s = c.get(f"/api/sessions?computer_id={cid}").json()[0]
    acts = {a["name"]: a for a in c.get(f"/api/sessions/{s['id']}").json()["actions"]}
    assert acts["FakeApp"]["status"] == "updated", acts["FakeApp"]
    assert acts["FakeApp"]["detected_before"] == "1.0" and acts["FakeApp"]["detected_after"] == "2.0"

    # Uninstall.
    c.put(f"/api/deployments/{dep['id']}", json={"item_type": "software", "item_id": sw["id"],
                                                 "target_type": "computer", "target_id": cid,
                                                 "desired": "uninstalled"})
    c.post(f"/api/computers/{cid}/maintenance", json={"mode": "apply"})
    assert run_agent(env, "once").returncode == 0
    assert not marker.exists()

    # Ad-hoc script with failure exit code.
    sid = c.post(f"/api/computers/{cid}/script", json={"script": "echo hello-from-mac\nexit 3"}).json()["session_id"]
    assert run_agent(env, "once").returncode == 0
    d = c.get(f"/api/sessions/{sid}").json()
    assert d["status"] == "failed" and d["actions"][0]["message"] == "exit code 3"
    assert any("hello-from-mac" in l["line"] for l in d["logs"])

    # Bad checksum is refused.
    c.post(f"/api/software/{sw['id']}/versions", json={"version": "3.0", "url": env["base"] + "/static/style.css",
                                                         "sha256": "0" * 64})
    c.put(f"/api/deployments/{dep['id']}", json={"item_type": "software", "item_id": sw["id"],
                                                 "target_type": "all", "desired": "latest"})
    c.post(f"/api/computers/{cid}/maintenance", json={"mode": "apply"})
    assert run_agent(env, "once").returncode == 0
    s = c.get(f"/api/sessions?computer_id={cid}").json()[0]
    a = c.get(f"/api/sessions/{s['id']}").json()["actions"][0]
    assert a["status"] == "failed" and "sha256 mismatch" in a["message"]
    assert not marker.exists()
