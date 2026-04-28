# Frontend Basics

Single source of truth for the frontend contract. This document consolidates the push-only protocol, view keys, WebSocket mechanics, JWT handling, reconnect strategy, and the recommended tech stack.

---

## 1. Design Philosophy

**Push-only, full-replacement.** The backend owns all state. The frontend is a thin renderer:

- Never poll for live data.
- Never merge or diff server payloads client-side.
- Always overwrite the local store entry with the full JSON the server sends.
- Historical data is fetched on-demand via REST; everything live arrives over one WebSocket.

---

## 2. Recommended Tech Stack

- **Framework**: Next.js 14+ (App Router) or React 18+ SPA
- **State management**: Zustand or Valtio (lightweight, no normalization needed)
- **WebSocket client**: Native `WebSocket` (no Socket.IO — the protocol is plain JSON)
- **HTTP client**: `fetch` or `axios` for the small set of REST calls
- **Styling**: TailwindCSS + shadcn/ui for rapid dashboards
- **Charts**: Lightweight canvas/SVG (e.g., `recharts` or `lightweight-charts` for PnL curves)

Why not Socket.IO / GraphQL subscriptions: the backend protocol is intentionally minimal (one WS, one JWT, JSON text frames). Adding a heavy client library adds bundle size with no benefit.

---

## 3. Authentication

### 3.1 Login Flow

```
POST /auth/login
Body: { "username": "...", "password": "..." }
```

On success, store the returned JWT securely (httpOnly cookie if SSR; `sessionStorage` if pure SPA). The JWT is required for **all** subsequent REST calls (`Authorization: Bearer <token>`) and for the WebSocket (`/stream?token=<JWT>`).

### 3.2 Token Refresh

The JWT expires at `expires_at`. Refresh proactively 60 seconds before expiry:

```
POST /auth/refresh   (send current JWT in header)
```

Response shape is identical to `/auth/login`. If refresh fails with `401`, redirect to login.

### 3.3 Upstox Token Webhook

The Upstox access-token approval callback is handled server-side at `POST /auth/upstox-webhook`. The frontend does **not** implement this. After approval, the backend stores the token and emits a `notification` over WS (see §5.5).

---

## 4. WebSocket Protocol

### 4.1 Endpoint

```
wss://<host>/stream?token=<JWT>
```

Only **one** concurrent connection per JWT is allowed. A new connection with the same JWT evicts the old one server-side.

### 4.2 Connection Sequence

1. Open WS.
2. Wait for `onopen`.
3. Send subscribe message:

```json
{
  "type": "subscribe",
  "views": [
    "dashboard",
    "position:nifty50",
    "position:banknifty",
    "positions:closed_today",
    "strategy:nifty50",
    "strategy:banknifty",
    "delta_pcr:nifty50",
    "delta_pcr:banknifty",
    "pnl",
    "capital",
    "health"
  ]
}
```

4. Server replies with a single **snapshot** containing the current value of every subscribed view:

```json
{
  "type": "snapshot",
  "ts": "2026-04-28T11:42:30+05:30",
  "data": {
    "dashboard":              { ... },
    "position:nifty50":       { ... },
    "position:banknifty":     null,
    "positions:closed_today": { "items": [...] },
    "strategy:nifty50":       { ... },
    ...
  }
}
```

### 4.3 Update Messages

After the snapshot, the server pushes an `update` every time any subscribed view changes:

```json
{
  "type": "update",
  "ts": "2026-04-28T11:42:31+05:30",
  "view": "position:nifty50",
  "data": { ... full new view payload ... }
}
```

**Semantics**: full replacement. The client must overwrite `store[view]` with `data` wholesale. No patching, no deep merging.

### 4.4 Subscription Mutation

Change subscriptions without reconnecting:

```json
{
  "type": "subscribe",
  "views": [ "dashboard", "capital", "health" ]
}
```

Server replaces the subscription set immediately. Any newly-added views receive an `update` within the next engine cycle; dropped views stop receiving messages.

### 4.5 Heartbeat

Server → Client every 20 s:

```json
{ "type": "ping", "ts": "..." }
```

Client must reply within 20 s:

```json
{ "type": "pong" }
```

If the client misses the pong window (server timeout = 30 s), the server closes the connection. The client should reconnect automatically (see §6).

### 4.6 Server-Initiated Notifications

Out-of-band events that are **not** view updates:

```json
{
  "type": "notification",
  "ts": "...",
  "level": "WARNING",
  "msg": "Broker portfolio WS disconnected; reconnecting"
}
```

Levels: `INFO`, `WARNING`, `CRITICAL`. Display `CRITICAL` in a persistent banner; log `INFO` / `WARNING` to a toast tray.

### 4.7 Close Codes

| Code | Meaning | Client Action |
|---|---|---|
| `1000` | Normal close (server shutdown) | Reconnect with backoff |
| `1008` | Auth invalid (JWT expired / bad) | Refresh JWT; if refresh fails, logout |
| `1011` | Server internal error | Reconnect with backoff |
| `4000` | Heartbeat timeout | Reconnect immediately |
| `4001` | Subscription validation failed | Fix subscribe payload and reconnect |

---

## 5. View Contract

Every screen has exactly one view key. The backend engine that owns the key rebuilds the JSON atomically and publishes it to `ui:pub:view`. FastAPI fans it out to all subscribed WS clients.

| View Key | Owner Engine | Shape Reference |
|---|---|---|
| `dashboard` | Background | §5.1 |
| `position:{index}` | Order Execution | §5.2 |
| `positions:closed_today` | Order Execution | §5.8 |
| `strategy:{index}` | Strategy | §5.3 |
| `delta_pcr:{index}` | Background | `API.md` §8.1 |
| `pnl` | Background | `API.md` §7.1 |
| `capital` | Background | §5.6 |
| `health` | Health Engine | `API.md` §9.1 |
| `configs` | Init + FastAPI | §5.9 |

### 5.1 `view:dashboard`

```json
{
  "system_state":   { "mode": "live", "trading_active": true },
  "total_pnl_today": 5412.5,
  "open_positions_count": 1,
  "trades_today": 7,
  "win_rate_today": 0.71,
  "indexes": {
    "nifty50":   { "state": "IN_CE", "pnl_today": 4162, "open": true },
    "banknifty": { "state": "FLAT",  "pnl_today": 1250, "open": false }
  },
  "health_summary": "OK",
  "ts": "..."
}
```

### 5.2 `view:position:{index}`

Either `null` (no open position) or the full position object:

```json
{
  "id": "uuid",
  "index": "nifty50",
  "side": "CE",
  "entry_at": "2026-04-28T09:15:12+05:30",
  "entry_premium": 12.5,
  "current_premium": 18.2,
  "running_pnl": 1425.0,
  "quantity": 75,
  "token": "NSE_FO|49520",
  "strike": 24500,
  "ts": "..."
}
```

### 5.3 `view:strategy:{index}`

```json
{
  "index": "nifty50",
  "enabled": true,
  "state": "IN_CE",
  "atm": 24500,
  "basket": {
    "ce": [
      { "strike": 24500, "token": "NSE_FO|49520", "pre_open": 142.5, "current": 158.0, "diff": 15.5 },
      { "strike": 24450, "token": "NSE_FO|49521", "pre_open": 178.0, "current": 195.0, "diff": 17.0 },
      { "strike": 24400, "token": "NSE_FO|49522", "pre_open": 215.0, "current": 232.0, "diff": 17.0 }
    ],
    "pe": [
      { "strike": 24500, "token": "...", "pre_open": 95.0,  "current": 78.0, "diff": -17.0 },
      ...
    ]
  },
  "sum_ce": 49.5,
  "sum_pe": -38.0,
  "current_position_id": "...",
  "trades_today": 4,
  "reversals_today": 1,
  "last_decision_ts": "..."
}
```

### 5.4 `view:delta_pcr:{index}`

See `API.md` §8.1. Contains the live ΔPCR value and 3-minute history for the index.

### 5.5 `view:pnl`

See `API.md` §7.1. Live PnL summary + per-index breakdown.

### 5.6 `view:capital`

```json
{
  "available_to_trade_total": 200000,
  "cash_available": 195000,
  "pledge_available": 5000,
  "margin_used": 12500,
  "kill_switch": {
    "any_engaged": false,
    "segments": { "NSE_FO": false, "BSE_FO": false }
  },
  "static_ips": {
    "primary":   "203.0.113.10",
    "secondary": "203.0.113.11"
  },
  "ts": "..."
}
```

### 5.7 `view:health`

See `API.md` §9.1. Engine-level health + dependency checks.

### 5.8 `view:positions:closed_today`

```json
{
  "items": [
    { ...full trades_closed_positions row... }
  ],
  "count": 7,
  "ts": "..."
}
```

### 5.9 `view:configs`

```json
{
  "execution":  { ... },
  "session":    { ... },
  "risk":       { ... },
  "indexes":    { ... },
  "ts": "..."
}
```

---

## 6. Reconnect Logic

Use an exponential-backoff loop with a ceiling:

```typescript
const MAX_RETRY_DELAY_MS = 30000;
let attempts = 0;

function connect() {
  const ws = new WebSocket(`wss://${HOST}/stream?token=${jwt}`);

  ws.onopen = () => {
    attempts = 0;
    ws.send(JSON.stringify({ type: "subscribe", views: getSubscribedViews() }));
  };

  ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));

  ws.onclose = (ev) => {
    if (ev.code === 1008) {
      // Auth failure — try refresh once, else logout
      refreshToken().then(connect).catch(logout);
      return;
    }
    const delay = Math.min(1000 * 2 ** attempts, MAX_RETRY_DELAY_MS);
    attempts++;
    setTimeout(connect, delay);
  };

  ws.onerror = () => { /* let onclose handle retry */ };
}
```

**Rules:**
- Reset `attempts` to `0` on every successful `onopen`.
- On `1008`, do not blind-retry. Attempt token refresh once.
- On `4000` (heartbeat timeout), reconnect immediately (no backoff).
- Preserve the user's current subscription list across reconnects so the snapshot restores the same UI state.

---

## 7. REST Endpoints the Frontend Uses

Live data is 100 % WebSocket. REST is used for:

| Use Case | Endpoint | Method |
|---|---|---|
| Login | `/auth/login` | `POST` |
| Token refresh | `/auth/refresh` | `POST` |
| Read config | `/configs` | `GET` |
| Update config section | `/configs/{section}` | `PUT` |
| Strategy status | `/strategy/status` | `GET` |
| Halt index | `/commands/halt_index` | `POST` |
| Resume index | `/commands/resume_index` | `POST` |
| Global halt | `/commands/global_kill` | `POST` |
| Global resume | `/commands/global_resume` | `POST` |
| Manual exit open position | `/commands/manual_exit/{position_id}` | `POST` |
| Position history | `/positions/history` | `GET` |
| Per-position report | `/reports/{position_id}` | `GET` |
| PnL history | `/pnl/history` | `GET` |
| ΔPCR history | `/delta_pcr/{index}/history` | `GET` |
| Health | `/health` | `GET` |

All endpoints require `Authorization: Bearer <JWT>` except `/auth/login` and `/auth/upstox-webhook`.

---

## 8. State Store Pattern (Reference)

```typescript
// Zustand store (minimal)
interface Store {
  views: Record<string, unknown>;
  notifications: Array<{ ts: string; level: string; msg: string }>;
  wsReady: boolean;
}

const useStore = create<Store>(() => ({
  views: {},
  notifications: [],
  wsReady: false,
}));

function handleMessage(msg: any) {
  if (msg.type === "snapshot") {
    useStore.setState({ views: msg.data, wsReady: true });
  } else if (msg.type === "update") {
    useStore.setState((s) => ({
      views: { ...s.views, [msg.view]: msg.data },
    }));
  } else if (msg.type === "notification") {
    useStore.setState((s) => ({
      notifications: [msg, ...s.notifications].slice(0, 100),
    }));
  }
}
```

No normalization, no selectors, no computed caches — the payloads are small and already shaped for the screen.

---

## 9. Rate Limits & CORS

- `POST /auth/login`: 5 / min
- `POST /commands/*`: 10 / min
- All other REST: 60 / min
- WebSocket: 1 active connection per JWT

Development CORS origin: `http://localhost:3000`. Production origin is configurable via `config:api.cors_origins`.

---

## 10. First-Boot Credential Wizard

The frontend must handle the case where Upstox creds are not yet configured. This is **not** an error state — it is the expected post-deploy state until the client enters their broker credentials.

### 10.1 Detecting the state

Read `view:health` (always pushed) and the `auth_status` field:

| `auth_status` | UI behavior |
|---|---|
| `valid` | Normal dashboard. No banner. |
| `missing` | Yellow banner: *"Upstox credentials not configured. Trading disabled. → Configure"*. Click → `/settings/broker`. |
| `invalid` | Red banner: *"Upstox credentials invalid. Trading disabled. → Fix"*. Click → `/settings/broker`. |
| `unknown` | Grey banner: *"Auth status pending — check back in a few seconds."* No action. |

The dashboard's other tiles (positions, PnL, etc.) render normally even in `missing` / `invalid` — they will just show empty / zero data. **Do not redirect or block the rest of the app.**

### 10.2 Settings → Broker → Upstox screen

Two modes:

**Mode A: no creds yet (`auth_status = missing`)**

Show a single form with the fields from `API.md` §3.5 request body. After submit:

```
POST /credentials/upstox
```

On 200 with `auth_status: "valid"` → show success toast, redirect to `/dashboard`.
On 200 with `auth_status: "invalid"` → show inline error using `broker_error.message`, leave the form populated for editing.
On 422 → render field-level pydantic errors next to each input.

**Mode B: creds present (`auth_status` ∈ `valid`/`invalid`)**

Pre-load the masked bundle:

```
GET /credentials/upstox
```

Render each field with its masked value as placeholder. The user types only the fields they want to change; on submit, send the **full** bundle (re-typing unchanged values is acceptable for a low-frequency settings screen). Provide a *"Reset all credentials"* button → `DELETE /credentials/upstox` with a confirm dialog.

### 10.3 Request Access Token button

Visible only when `auth_status ∈ valid/invalid` (creds present). Calls:

```
POST /commands/upstox_token_request
```

Then displays a modal: *"Approve the request in your Upstox app or WhatsApp within 10 minutes. This page will update automatically once approval lands."*

Listen on the WebSocket for `notification` messages with `level: INFO` and a known message body containing `auth_refreshed`, OR poll `view:health` for `auth_status` flipping to `valid`. Either signal closes the modal.

### 10.4 Degraded-mode behavior summary

| Component | `auth_status = valid` | `auth_status ∈ missing/invalid` |
|---|---|---|
| Dashboard tiles | Live data | Empty / zero |
| WebSocket | Subscribes to all views | Same — subscribes anyway |
| Settings | Read/write all sections | Read/write all sections (Broker is the focus) |
| `/commands/*` | Allowed | Disallowed by backend (returns `409 TRADING_DISABLED`) — frontend should grey out the buttons |
| Banner | Hidden | Shown |

---

## 11. Common Pitfalls

1. **Do not poll `/strategy/status` in a `setInterval`.** Subscribe to `view:strategy:{index}` on the WS and render from the store.
2. **Do not diff or merge view payloads.** Always replace the store entry.
3. **Do not forget the heartbeat `pong`.** Missing it causes a server-side close every 30 s.
4. **Do not store the JWT in `localStorage` if avoidable.** Prefer httpOnly cookies (SSR) or `sessionStorage` (SPA) to mitigate XSS.
5. **Do not assume a view key will always have data.** `view:position:{index}` is `null` when flat; render the "No open position" screen without fetching REST.
