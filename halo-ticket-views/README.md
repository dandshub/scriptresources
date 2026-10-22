# Halo Ticket Views – Mobile App

A mobile-friendly web app (PWA) for viewing HaloPSA custom ticket views on a phone, built to replace the official Halo mobile app for day-to-day ticket triage.

## Where we are

**Status: clickable UI mock-up only. Not connected to Halo yet.**

| Area | State |
|---|---|
| UI / UX mock-up | Done (`index.html`) |
| Sample data | Hard-coded in `index.html` (9 fictional tickets, 4 agents) |
| Halo API connection | Not started |
| Authentication | Not started (approach decided below, pending your choice) |
| Backend proxy | Not started |
| PWA install (manifest, icons) | Not started |
| Hosting | Not decided |

Online preview of the mock: https://claude.ai/artifact/Q2eUPrZgaQHtkaRn4jkvA1 (private until shared).

## Files

| File | Purpose |
|---|---|
| `index.html` | The whole mock-up in one file: HTML, CSS and JS. No build step and no dependencies apart from Google Fonts (IBM Plex Sans / Mono). Open it in a browser, or serve it with `npx serve .` / `python3 -m http.server`. |
| `README.md` | This handover. |

## What the mock-up does

- **View chips** along the top (My Open Tickets, Unassigned, SLA at Risk, Awaiting Client, P1 / P2, All Open), each with a count. These stand in for your Halo custom views. The last view you picked is saved in `localStorage`.
- **Search** across ticket ID, summary, client, user and category.
- **Sort** by SLA due, priority or newest.
- **Ticket cards** have a priority colour edge (P1 red, P2 amber, P3 blue, P4 grey). They show an SLA timer ("Fix in 45m", "Fix breached 12m", or "SLA paused" for Awaiting Client / On Hold), status, assigned agent and the ticket's age.
- **Ticket detail** opens as a slide-over panel with:
  - an SLA progress bar
  - contact details (client, site, user, phone, email) with copy buttons
  - type, category and agent
  - the timeline of emails, notes, private notes and alerts, newest first
- **Actions** (in memory only, lost on reload):
  - change status
  - take or reassign the ticket
  - add a note, with a private/public toggle
- Light and dark mode follow the device setting.
- Laid out for phones: safe-area insets, sticky header, bottom tab bar, and a max-width 480px column on desktop.
- **Placeholders:** the New, Alerts and Me tabs only show a "not in the mock yet" message.

### Code map (inside `index.html`)

- `:root` CSS tokens: all colours for light and dark mode are defined here.
- `tickets` array: sample data. Its shape roughly follows a Halo ticket (see the field mapping below).
- `VIEWS` array: the view chips. Each one has a JS filter `f` for now. In the real app each one maps to a Halo view ID instead.
- `renderChips()`, `renderList()`, `renderDetail()`: render the UI from `state`.
- A single delegated `click` handler covers every interaction (sheets, status, assignment, notes).

## Next steps

1. **Settle the UI.** Decide which fields matter on the cards, list your real custom view names, and choose view-only or view plus actions.
2. **Create a Halo API application.** In Halo: Configuration → Integrations → Halo API → View Applications → New.
   - **Recommended: per-agent login.** Use the *Authorization Code (PKCE)* flow. Each tech signs in as themselves and sees what their permissions allow, and Halo attributes actions to them. Personal (non-shared) views only work this way.
   - **Alternative: shared service account.** Use *Client ID and Secret (Services)*. It's simpler, but everyone sees the same data, and the secret must stay on a server.
   - Permissions: read tickets and actions. Add edit tickets and add actions if notes and status changes stay in scope.
3. **Add a small backend proxy** (Node/Express, an Azure Function or a Cloudflare Worker). It does three jobs: holds secrets, gets around CORS, and caches the view list. Skip it only if your Halo instance allows CORS for SPA clients.
4. **Replace the sample data with API calls** (confirm the exact parameter names in your tenant's swagger at `https://<tenant>.halopsa.com/api/swagger` or `/apidoc`):
   - Ticket views list: Halo's view and filter-profile endpoints. Each `VIEWS` entry becomes `{id: <halo view id>, name}`.
   - Tickets in a view: `GET /api/Tickets?view_id=<id>&pageinate=true&page_size=50&page_no=1&order=...&search=...`
   - Ticket detail: `GET /api/Tickets/{id}?includedetails=true`
   - Timeline: `GET /api/Actions?ticket_id={id}`
   - Add note or change status: `POST /api/Actions` (with `outcome`, `note`, `hiddenfromuser` for private) and `POST /api/Tickets`, sending `id` plus the changed fields.
5. **Make it a PWA:** add `manifest.webmanifest`, icons and a service worker that caches the last-loaded view for offline use. Then use "Add to Home Screen".
6. **Host it** somewhere reachable from phones, behind HTTPS (for example Azure Static Web Apps with a Functions API, or Cloudflare Pages with Workers).

### Mock field → Halo field mapping (to verify against your tenant)

| Mock field | Likely Halo field |
|---|---|
| `id` | `id` |
| `sum` | `summary` |
| `client` / `site` / `user` | `client_name` / `site_name` / `user_name` |
| `type` | `tickettype_name` (or lookup via `tickettype_id`) |
| `pri` | `priority_id` |
| `status` | `status_name` (or lookup via `status_id`) |
| `agent` | `agent_id` / `agent_name` |
| `team` | `team` |
| `cat` | `category_1` |
| `opened` | `dateoccurred` |
| `due` / `dueType` | `fixbydate`, `respondbydate`, `slastate`, `sla_hold` |
| `notes[]` | `/api/Actions`: `who`, `datetime`, `note`, `hiddenfromuser`, `outcome` |

## Open decisions

- Per-agent login (PKCE) or a shared service account?
- View-only, or view plus actions (notes, status, assignment)?
- Hosting platform?
- Are your custom views shared or personal? Personal views need per-agent login.
- Halo cloud or self-hosted? The auth URL differs.
