# Level Tech Chat (3CX Live Chat pop-up for Level.io)

Level.io has no built-in chat during a remote session. This bolts one on using
your **existing 3CX Live Chat**: when you connect to a machine, you click a
button in Level and a small chat window opens on the end-user's desktop. They
type; you answer from your normal 3CX Web Client / app.

It's **tech-initiated** — nothing runs in the background, and you decide exactly
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

| File | Purpose |
|------|---------|
| `Open-LevelTechChat.ps1` | On-demand Level script — pops the chat window |
| `Close-LevelTechChat.ps1` | Optional — closes just that window when you're done |
| `chat.html` | The page that hosts your 3CX Live Chat widget |

## How the 3CX bit works

3CX Live Chat gives you a **website embed snippet** (a `<call-us>` widget), not a
standalone link — so the widget has to sit on an HTML page. That's what
`chat.html` is. There are two ways to deliver it:

### Option 1 — Host it (recommended)

1. Upload `chat.html` to your website, e.g. `https://dsbusinesshub.co.uk/support-chat.html`.
2. In 3CX → **Live Chat** → your source → set **"Your website"** to that domain
   (`https://dsbusinesshub.co.uk`) and **Save**.
3. In `Open-LevelTechChat.ps1`, set `$HostedChatUrl` to the page URL.

Fully 3CX-supported, no surprises. This is the safe bet.

### Option 2 — Local, zero hosting (quick)

1. Leave `$HostedChatUrl` blank in `Open-LevelTechChat.ps1`.
2. Confirm `$PbxUrl` and `$Party` match your embed snippet (already set to
   `https://1303.3cx.cloud` / `LiveChat595749`).

The script drops a local copy of the page on the PC and opens it. Usually works,
but 3CX **can** refuse to connect from a `file://` page if its domain restriction
is strict — if the chat window loads but never connects, use Option 1.

## Recommended 3CX Live Chat settings

In the source you're editing (the "Add Live Chat" screen):

| Setting | Set to | Why |
|---------|--------|-----|
| **Destination** | Dept DS Business Hub Ltd | Where chats ring — make sure agents are logged in |
| **Your website** | your hosting domain (Option 1) or your main site | Required; also the domain restriction |
| **What visitor info to collect** | **None** (or Name only) | Skips the pre-chat form so the user chats immediately |
| **Allow calls and chats** | **Chat Only** | We want a text window (leave on Phone + Chat if you also want click-to-call) |
| **Startup Mode** | any (the page forces the chat open) | `chat.html` sets `minimized="false"` |

If you regenerate the snippet in 3CX, replace the `<call-us ...>` block and the
`<script ...>` line in `chat.html` (and update `$PbxUrl` / `$Party` if you use
Option 2). The values that matter: `phonesystem-url` and `party`.

## Setup summary

1. Pick Option 1 or 2 above and set `$HostedChatUrl` (or `$PbxUrl`/`$Party`).
2. Add `Open-LevelTechChat.ps1` (and optionally `Close-LevelTechChat.ps1`) as
   **on-demand scripts** in Level.io — run as SYSTEM (the default).

## Day-to-day use

1. Connect to the machine as usual.
2. Run the **Open** script from Level → the chat window appears for the user.
3. Chat with them from your 3CX Web Client / app.
4. Run the **Close** script when finished (or the user just closes the window).

## Quick heads-up alternative (zero setup, one-way)

If you only need to tell the user "I'm here" and don't need a reply, this is built
into Windows and needs no 3CX at all:

```powershell
msg * "Hi, it's Dan from support - I'm connected and taking a look now."
```

## Prerequisites

- **Microsoft Edge** (on all supported Windows 10/11 — used in a borderless "app"
  window; falls back to the user's default browser if Edge is missing).
- Internet access on the PC (the widget loads `callus.js` from 3CX's CDN).

## Notes & limits

- The window is a normal Edge "app" window (small title bar, no address bar),
  running in its own tagged Edge profile so it won't touch the user's browsing —
  and that tag is how the Close script targets only this window.
- Requires a user **logged on** at the PC (no one to chat with otherwise). If no
  one is logged on, Open reports that and exits.
- Chats route to whoever mans the 3CX Live Chat queue for this source. To reach a
  specific tech, point at that tech's own Live Chat source or handle routing in 3CX.
