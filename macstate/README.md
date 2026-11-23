# MacState

Desired-state software deployment and configuration management for **macOS**,
built for MSPs. It covers the same ground as ImmyBot does for Windows: you say
what should be true on each Mac (which apps at which version, which settings),
and maintenance sessions bring every Mac to that state, with full logs.

* **Tenants**: one per client. Each has an enrollment key and a one-line install command.
* **Software library**: detection rules (app bundle, pkg receipt or script) plus
  installers (`.pkg`, `.dmg`, `.zip` or a custom script). You can pin versions with
  optional SHA-256 checksums, or use a rolling "latest" download URL.
* **Maintenance tasks**: desired-state configuration scripts. A *test* script
  exits 0 when the Mac is compliant, and a *set* script fixes it. Parameters
  are passed as environment variables and can be overridden per deployment.
* **Deployments**: put software or a task on all computers, a tenant, a tag or
  one computer. Desired states: *Latest*, *Installed (any)*, *Specific version*
  (downgrades when needed), *Uninstalled*, *Ignore*. Tasks use *Enforce* or
  *Monitor*. If several deployments match, the most specific one wins
  (computer › tag › tenant › all).
* **Maintenance sessions**:
  * Kinds: **onboarding** (runs automatically when a Mac enrolls), **manual**,
    **scheduled**, and **ad-hoc script**.
  * Modes: **apply** or **detect-only**.
  * Each action is logged live, with the detected state before and after.
  * Sessions can be cancelled. Offline Macs pick up queued sessions when they
    next check in.
* **Schedules**: maintenance windows by time and weekday for any target.
* **Inventory**: model, serial, OS and build, CPU, RAM, disk, FileVault,
  Gatekeeper and SIP status, the console user, and installed apps with versions
  and bundle IDs.
* **Compliance view**: per Mac and fleet-wide, from the last detection.
* **Users**: *admin* (everything) and *technician* (can run maintenance and
  manage deployments, but cannot edit the library or run raw scripts).
* **Agent self-update**: rebuild the agent on the server and every Mac updates
  itself after verifying a checksum.

```
┌────────────────────────┐   HTTPS (poll every 60s)  ┌──────────────────────────┐
│ MacState server        │ ◀──────────────────────── │ macstate-agent (Go)      │
│ FastAPI + SQLite       │   check-in / inventory    │ root LaunchDaemon        │
│ web UI, scheduler      │ ────────────────────────▶ │ detect → install/remove  │
│ /agent/install.sh      │   session actions         │ → re-detect → report     │
└────────────────────────┘                           └──────────────────────────┘
```

The agent only makes outbound requests, so Macs need no inbound ports and work
behind NAT.

## Quick start (server)

The server runs anywhere Python 3.10+ runs. It is usually a small Linux VM
behind a TLS reverse proxy.

```bash
cd macstate
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./build-agent.sh                       # needs Go 1.22+; outputs build/macstate-agent-darwin-{arm64,amd64}
MACSTATE_PUBLIC_URL=https://macstate.example.com \
MACSTATE_ADMIN_PASSWORD='change-me-now' \
MACSTATE_HTTPS_COOKIES=1 \
./run.sh                               # listens on 127.0.0.1:8080
```

If `MACSTATE_ADMIN_PASSWORD` is not set, a random password for `admin` is printed
on first start. On first start the library is also seeded with some common apps
(Chrome, Firefox, Zoom, VS Code, Teams, Homebrew) and baseline tasks (Firewall,
Gatekeeper, FileVault monitor, automatic updates, computer naming).

Put a reverse proxy in front of it (Caddy example):

```
macstate.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MACSTATE_PUBLIC_URL` | from request | URL that Macs use to reach the server (baked into install commands) |
| `MACSTATE_DB` | `data/macstate.db` | SQLite database path |
| `MACSTATE_AGENT_DIR` | `build/` | Where `build-agent.sh` puts agent binaries |
| `MACSTATE_ADMIN_PASSWORD` | random | Initial `admin` password (used on first start only) |
| `MACSTATE_HTTPS_COOKIES` | `0` | Set to `1` behind HTTPS so the login cookie is Secure |
| `MACSTATE_POLL_SECONDS` | `60` | Agent check-in interval |
| `MACSTATE_ONLINE_SECONDS` | `300` | How recently a Mac must have checked in to count as online |
| `MACSTATE_SESSION_TIMEOUT` | `21600` | A running session with no progress for this many seconds is failed |
| `MACSTATE_SCHEDULER` | `1` | Set to `0` to disable schedules |
| `MACSTATE_SEED` | `1` | Set to `0` to skip the starter library |
| `MACSTATE_HOST` / `MACSTATE_PORT` | `127.0.0.1` / `8080` | Bind address (`run.sh`) |

## Enrolling Macs

Open **Tenants** and copy the install command:

```bash
curl -fsSL 'https://macstate.example.com/agent/install.sh?key=TENANT_KEY' | sudo bash
```

The command works in Terminal, and as a root shell script in Jamf, Intune,
Kandji, Mosyle or any other MDM. It does the following:

1. Downloads the agent for the Mac's architecture to `/Library/Application Support/MacState/`.
2. Enrolls the Mac in the tenant. Re-installing on the same serial number
   reuses the existing record.
3. Installs and starts the `com.macstate.agent` LaunchDaemon. The log is at
   `/Library/Logs/MacState/agent.log`.

If the tenant has onboarding on, the Mac runs a full maintenance session right
away.

Agent commands (run as root):

```bash
A='/Library/Application Support/MacState/macstate-agent'
sudo "$A" once         # check in now and run any queued session
sudo "$A" inventory    # print what the agent collects
sudo "$A" uninstall    # remove agent, config and LaunchDaemon
"$A" version
```

## How the agent applies software

For each software action:

1. **Detect** the current state:
   * `app`: the bundle exists, and its version is read from `CFBundleShortVersionString`.
   * `pkg`: `pkgutil --pkg-info`.
   * `script`: exit 0 and print the version, or exit non-zero if not installed.
2. If the detected state already matches the desired state, report **compliant**.
   In detect-only mode, report **noncompliant** and stop.
3. **Install, update or uninstall**:
   * The installer is downloaded and checked against its SHA-256 if one is set.
   * `pkg`: `installer -pkg … -target /`.
   * `dmg`: mounted with `hdiutil`. The agent copies the app named by the
     detection rule into place with `ditto`, or runs a `.pkg` found inside.
   * `zip`: unpacked with `ditto -x -k`, then handled like a `dmg`.
   * `script`: your install script runs, with `MS_INSTALLER_PATH` pointing at
     the download.
   * Uninstall runs your uninstall script, or removes the app bundle for
     `app` detection.
4. **Re-detect** and report **installed**, **updated**, **uninstalled** or **failed**.

Scripts run as root, under `/bin/zsh` unless they start with a `#!` line. They
receive these environment variables: `MS_CONSOLE_USER`, `MS_TARGET_VERSION`,
`MS_DETECTED_VERSION`, `MS_APP_PATH`, `MS_INSTALLER_PATH`, `MS_DOWNLOAD_DIR`,
plus any task parameters by name.

Versions compare segment by segment, numerically, and a letter suffix counts as
a pre-release. So `1.0b1` < `1.0` < `1.0.1` < `1.10`.

## Development

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests     # API tests, plus an end-to-end test that builds and runs the real agent
(cd agent && go test ./...)
```

The end-to-end test runs the agent on Linux, using script-based detection and
installers. The macOS-specific handlers (`installer`, `hdiutil`, `ditto`,
`plutil`) need a real Mac to test.

## Security notes

* Agents run as root and execute scripts from the server. **Anyone with admin
  access to MacState can run code on every enrolled Mac.** Protect the server
  the way you would protect your RMM: use HTTPS only, strong passwords, and
  ideally an IP allow-list or SSO proxy in front of the web UI. The `/api/agent/*`
  and `/agent/*` paths must stay reachable by the Macs.
* Agent tokens are random 256-bit values. Only their SHA-256 is stored.
  Passwords use PBKDF2-SHA256.
* An enrollment key lets someone enroll a Mac into a tenant. Rotate the key if
  it leaks; Macs that are already enrolled are not affected.
* Add SHA-256 checksums to pinned versions so a compromised download host can't
  push a different installer.
* The agent binary is ad-hoc signed by the Go linker. For production, sign it
  with a Developer ID and notarize it. You can also wrap it in a signed `.pkg`
  for MDM deployment, and grant it Full Disk Access through a PPPC profile if
  your scripts need it.
