# Level Tech Chat (3CX Live Chat pop-up for Level.io)

Level.io has no built-in chat during a remote session. This bolts one on using
your **existing 3CX Live Chat**: a small chat window opens on the end-user's
desktop, and the tech answers from their normal 3CX Web Client / app. No new
servers, no accounts.

Why 3CX? It already owns both ends (the widget on the PC + your 3CX client for
the tech) — which is exactly why chat "just works" in TeamViewer/Splashtop/etc.
Anything on top of Level is a bolt-on, so the only real question is **what makes
the window appear**. There are three approaches, simplest first.

> **The session-0 gotcha:** Level runs scripts as **SYSTEM**, and SYSTEM can't
> draw a window on the user's desktop (session-0 isolation). Every option below
> deals with this for you.

---

## Option A — On-demand, one click (recommended, simplest)

**`Open-LevelTechChat.ps1`** — an on-demand Level script the tech runs the
moment they connect. It pops the 3CX chat window in the user's session, then
cleans up. **Nothing is installed, nothing runs in the background.**

1. Edit `$ChatUrl` (your 3CX Live Chat share link — see below).
2. In Level, save it as an **on-demand script**.
3. When you connect to a machine, click **Run**. Chat appears.

This is the sweet spot: reliable, zero footprint, and the tech controls exactly
when it shows.

## Option B — Quick heads-up (zero install, one-way)

Built into Windows, works from a SYSTEM Level script today:

```powershell
msg * "Hi, it's Dan from support - I'm connected and taking a look now."
```

`msg.exe` pops a message box on the user's screen. One-way only (they can't
type back), but perfect for a quick "I'm here" without any chat setup.

## Option C — Auto-detect (advanced, optional)

**`Install-LevelTechChat.ps1`** — installs a lightweight background watcher that
opens the chat **automatically** when it detects a Level remote session, with no
tech action at all. More moving parts, and it depends on you pinning down Level's
remote-session **process name** on your fleet (see below). Use this only if you
want it fully hands-off. Remove it with `Uninstall-LevelTechChat.ps1`.

---

## Get your 3CX Live Chat link (needed for A and C)

1. 3CX Admin Console → **Live Chat** → open (or create) your chat **source**.
2. **Share → Copy Link** — you'll get something like
   `https://yourcompany.3cx.eu/callus/#/<source-id>`.
3. Paste it into a browser to confirm it opens a working chat, then drop it into
   `$ChatUrl`.

## Prerequisites

- **Microsoft Edge** (on all supported Windows 10/11 — used in borderless "app"
  mode; falls back to the default browser if absent).
- A **3CX Live Chat** source configured in your 3CX admin console.

---

## Option C details (auto-detect)

### What gets installed

Everything lands in `C:\ProgramData\DSBusinessHub\LevelTechChat\`:

| File | Purpose |
|------|---------|
| `Config.json` | Settings written from the installer's **EDIT ME** block |
| `Watcher.ps1` | Runs in the logged-on user's session; opens/closes the window |
| `run-hidden.vbs` | Launches the watcher with no console flash |
| `watcher.log` | Debug log (when `DebugMode = 1`) |

A per-user scheduled task **`DSBusinessHub-LevelTechChat`** starts the watcher at
logon, running as the **interactive user** (so it can draw the window).

### Find the Level remote-session process (important)

The watcher decides "a session is live" by looking for a process that **only
exists while a Level remote session is running**. This name can change between
Level agent versions, so **verify it**:

1. Start a real Level remote-control session to a test PC.
2. On that PC: **Task Manager → Details**, and find the new Level helper `.exe`
   that appears — and disappears when you end the session.
3. Put its name in `$RemoteSessionProcesses`.

> Do **not** list the always-on Level agent (e.g. `level` / `level-agent`) — that
> runs 24/7 and would keep the chat open constantly.

**Can't pin it down?** Use the manual trigger — the watcher also opens the window
whenever this file exists:
`C:\ProgramData\DSBusinessHub\LevelTechChat\open.trigger`

```powershell
# open now
New-Item "C:\ProgramData\DSBusinessHub\LevelTechChat\open.trigger" -ItemType File -Force | Out-Null
# close again
Remove-Item "C:\ProgramData\DSBusinessHub\LevelTechChat\open.trigger" -Force -EA SilentlyContinue
```

### Config reference (`Install-LevelTechChat.ps1` → EDIT ME)

| Setting | Meaning |
|---------|---------|
| `$ChatUrl` | Your 3CX Live Chat share link (required) |
| `$RemoteSessionProcesses` | Process name(s) that mean "session is live" |
| `$CloseOnDisconnect` | Auto-close the window when the session ends |
| `$PollSeconds` | How often to check (default 3s) |
| `$WindowWidth` / `$WindowHeight` | Chat window size |
| `$DebugMode` | `1` writes `watcher.log`; `0` is silent |

### Uninstall

Run `Uninstall-LevelTechChat.ps1` from Level as SYSTEM. It removes the task, stops
the watcher and any chat windows, and deletes the install folder and dedicated
Edge profile.

---

## Notes & limits

- The window is a normal Edge "app" window (small title bar, no address bar).
- **Recommendation:** start with **Option A**. It's the least to go wrong, and the
  tech triggering it on connect is more reliable than guessing Level's process
  name. Keep Option B (`msg.exe`) for quick notices.
- End-users can close the window; the tech can reopen it by running Option A again.
