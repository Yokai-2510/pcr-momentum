# Frontend Integration Guide

The frontend (Vercel-hosted) talks to the backend (EC2) over HTTPS + WSS.
The backend is push-only: every screen has a "view key" the server rebuilds
and pushes whole; the client just stores it. No business logic in the client.

---

## 1. Endpoints

| Purpose | URL |
|---|---|
| REST base | `https://3-6-128-21.sslip.io/api/` |
| WebSocket | `wss://3-6-128-21.sslip.io/stream?token=<JWT>` |
| OpenAPI / Swagger | `https://3-6-128-21.sslip.io/api/docs` |
| Health (no auth) | `https://3-6-128-21.sslip.io/api/health` |

Frontend env:

```
NEXT_PUBLIC_API_BASE=https://3-6-128-21.sslip.io/api
NEXT_PUBLIC_WS_URL=wss://3-6-128-21.sslip.io/stream
```

CORS allows any `https://*.vercel.app` origin (regex). No allowlist
maintenance needed when preview deployments rotate URLs.

---

## 2. Auth flow

Single user, JWT-based.

```
POST /api/auth/login
  body: {"username": "...", "password": "..."}
  resp: {"token": "<jwt>", "expires_at": "ISO-8601", "user": {...}}
```

- Send the token on **every** REST request as `Authorization: Bearer <jwt>`.
- For the WebSocket, append `?token=<jwt>` to the URL (server reads it at handshake).
- Token expiry default 12 h. Refresh with `POST /api/auth/refresh` (Bearer header, empty body) before expiry.
- On 401, drop the token and route to `/login`.

Recommended client storage: `sessionStorage` (cleared on tab close) over `localStorage` (XSS blast radius).

---

## 3. WebSocket protocol (the only push channel)

After handshake, send one subscribe frame:

```json
{
  "type": "subscribe",
  "views": [
    "dashboard",
    "position:nifty50", "position:banknifty",
    "positions:closed_today",
    "strategy:nifty50",  "strategy:banknifty",
    "delta_pcr:nifty50", "delta_pcr:banknifty",
    "pnl", "capital", "health"
  ]
}
```

Server replies once with `{"type":"snapshot","data":{view: payload, ...}}`.
Then on every change to any subscribed view it sends:

```json
{ "type":"update", "view":"position:nifty50", "data": { ...full payload... } }
```

**Full replacement only** — never partial diffs. Client store is just `store[msg.view] = msg.data`.

Also expect:
- `{"type":"ping","ts":"..."}` every 20 s — reply `{"type":"pong"}` within 30 s or server closes 4000.
- `{"type":"notification","level":"WARNING|CRITICAL","msg":"..."}` for ad-hoc alerts. Show a toast.

Reconnect:
- Close codes 1008 / 4000 / 4001 → token problem; redirect to `/login`.
- Any other close → exponential backoff (1 s, 2 s, 4 s, max 30 s), reconnect, send the same subscribe frame; server returns a fresh snapshot. Don't try to "catch up" missed updates.

Full view-payload schemas in [`API.md`](./API.md) §12.

---

## 4. REST surface

27 endpoints; full schemas in [`API.md`](./API.md). Mental map:

| Group | Endpoints | Notes |
|---|---|---|
| Auth | `/auth/login`, `/auth/refresh`, `/auth/upstox-webhook` | Webhook is public; others require JWT |
| Credentials | `GET/POST/DELETE /credentials/upstox` | First-boot flow + rotation |
| Configs | `GET /configs`, `GET/PUT /configs/{section}` | Sections: `execution`, `session`, `risk`, `index:nifty50`, `index:banknifty` |
| Strategy | `GET /strategy/status`, `POST /commands/halt_index/{idx}`, `/commands/resume_index/{idx}`, `/commands/global_kill`, `/commands/global_resume` | |
| Positions | `GET /positions/open`, `/positions/closed_today`, `/positions/history` (paged), `/reports/{id}`, `POST /commands/manual_exit/{id}` | History → Postgres; live → Redis |
| PnL | `GET /pnl/live`, `/pnl/history` | |
| ΔPCR | `GET /delta_pcr/{idx}/live`, `/delta_pcr/{idx}/history`, `PUT /delta_pcr/{idx}/mode` | Mode 1=display, 2=soft veto, 3=hard veto |
| Health | `GET /health` (no auth), `/health/dependencies/test` | Dashboard health strip |
| Capital | `GET /capital/funds`, `GET/POST /capital/kill_switch` | Live broker margin + segment kill switch |
| Operations | `POST /commands/instrument_refresh`, `/commands/upstox_token_request` | |

All bodies JSON, datetimes ISO-8601 with TZ, currency INR numeric.

---

## 5. Error envelope

Every non-2xx response:

```json
{ "error": { "code": "ERROR_CODE", "message": "...", "details": { "field": "..." } } }
```

HTTP codes used: 400 / 401 / 403 / 404 / 409 / 422 / 429 / 500 / 503. Surface `error.message` to the user; log the rest.

---

## 6. Rate limits

| Bucket | Limit |
|---|---|
| `POST /auth/login` | 5 / min |
| `POST /commands/*` | 10 / min |
| All other | 60 / min |
| WebSocket | 1 active per JWT |

429 responses include `Retry-After`.

---

## 7. Daily lifecycle the UI must reflect

Cyclic engines run only Mon-Fri 08:00–15:46 IST. Outside that window the API is still up (FastAPI + Postgres + Redis + Nginx are 24×7) but live views read mostly null and the WS push channel goes silent.

- Show a "Market closed — last session summary" banner when `view:dashboard.system_state.trading_active === false` AND it's outside trading hours.
- Read `trading_disabled_reason` from the dashboard view to pick banner copy: `none | awaiting_credentials | auth_invalid | holiday | manual_kill | circuit_tripped`. Credentials wizard reachable iff `awaiting_credentials | auth_invalid`.
- Historical screens always work; they hit Postgres directly.

---

## 8. Local dev tunnel

To iterate locally without TLS/CORS friction:

```powershell
ssh -i .\nse_index_pcr_trading_pemkey.pem -L 8000:127.0.0.1:8000 -N ubuntu@3.6.128.21
# in another shell:
NEXT_PUBLIC_API_BASE=http://localhost:8000 NEXT_PUBLIC_WS_URL=ws://localhost:8000/stream pnpm dev
```

Production deploys on Vercel just use the sslip.io URLs above.

---

## 9. What not to do

- Don't merge / diff WS payloads. Replace whole.
- Don't poll REST endpoints that have a corresponding view. Use the WS.
- Don't store the JWT in `localStorage` if any third-party scripts are loaded.
- Don't compute trading state in the client. Render `view:position:{index}` as-is.
- Don't retry on 401. Drop the token, redirect to login.
- Don't rely on the WS for history — that goes through REST → Postgres.
