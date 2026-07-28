# Level Tech Chat (3CX Live Chat pop-up for Level.io)

Level.io has no built-in chat during a remote session. This bolts one on using
your **existing 3CX Live Chat**: when you connect to a machine, you click a
button in Level and a small chat window opens on the end-user's desktop. They
type; you answer from your normal 3CX Web Client / app.

Why 3CX? It already owns both ends (the widget on the PC + your 3CX client for
the tech) — which is exactly why chat "just works" in TeamViewer/Splashtop/etc.
It's **tech-initiated**: nothing runs in the background, and you decide exactly
when the window appears.

```
[You connect] -> run Open-LevelTechChat in Level -> 3CX chat window pops up
                                                     on the user's desktop
[You finish]  -> run Close-LevelTechChat (optional) -> window closes
```

> **The session-0 gotcha:** Level runs scripts as **SYSTEM**, and SYSTEM can't
> draw a window on the user's desktop (session-0 isolation). The open script
> handles this by briefly borrowing the logged-on user's session to launch the
> window, then cleaning up. You don't have to do anything about it.

## What's here

| Script | When you run it |
|--------|-----------------|
| `Open-LevelTechChat.ps1` | On-demand, the moment you connect — pops the chat window |
| `Close-LevelTechChat.ps1` | Optional, when you're done — closes just that window |

## Setup (one-time)

### 1. Get your 3CX Live Chat link

1. 3CX Admin Console → **Live Chat** → open (or create) your chat **source**.
2. **Share → Copy Link** — you'll get something like
   `https://yourcompany.3cx.eu/callus/#/<source-id>`.
3. Paste it into a browser to confirm it opens a working chat.

### 2. Put it in the script

Edit `$ChatUrl` at the top of `Open-LevelTechChat.ps1` with that link.
(Optionally adjust `$WindowWidth` / `$WindowHeight`.)

### 3. Save it in Level

Add `Open-LevelTechChat.ps1` as an **on-demand script** in Level.io (run as
SYSTEM — the default). Do the same with `Close-LevelTechChat.ps1` if you want a
one-click close.

## Day-to-day use

1. Connect to the machine as usual.
2. Run the **Open** script from Level → the chat window appears for the user.
3. Chat with them from your 3CX client.
4. Run the **Close** script when finished (or the user can just close the window).

## Quick heads-up alternative (zero setup, one-way)

If you only need to tell the user "I'm here" and don't need them to reply, this
is built into Windows and works from a SYSTEM Level script with no 3CX at all:

```powershell
msg * "Hi, it's Dan from support - I'm connected and taking a look now."
```

## Prerequisites

- **Microsoft Edge** (on all supported Windows 10/11 — used in a borderless
  "app" window; falls back to the user's default browser if Edge is missing).
- A **3CX Live Chat** source configured in your 3CX admin console.

## Notes & limits

- The window is a normal Edge "app" window (small title bar, no address bar). It
  runs in its own tagged Edge profile, so it won't touch the user's browsing
  session — and that tag is how the Close script targets only this window.
- Requires a user to be **logged on** at the PC (there's no one to chat with
  otherwise). If no one is logged on, the Open script says so and exits.
- The chat routes to whoever is manning your 3CX Live Chat queue. If you want it
  to reach the specific connecting tech, point `$ChatUrl` at that tech's own
  Live Chat source, or handle routing in 3CX.
