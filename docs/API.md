# API Specification — FastAPI Gateway

This document specifies every endpoint exposed by the FastAPI Gateway: request shapes, response shapes, error codes, authentication, and the WebSocket protocol. Frontend developers should be able to build the entire client/server contract from this document alone.

---

## 1. Base

- **Base URL** (local): `http://localhost:8000`
- **Base URL** (deployed): `https://<host>:8443`
- **All request/response bodies**: JSON, UTF-8, `application/json`
- **All datetimes**: ISO-8601 with timezone (`2026-04-28T09:15:00+05:30`)
- **All currency values**: numeric, in INR
- **Auth**: JWT in `Authorization: Bearer <token>` header on every endpoint except `/auth/login` and `/auth/upstox-webhook`

## 2. Standard Error Response

Every endpoint may return:

```json
{
  "error": {
    "code": "ERROR_CODE_CONSTANT",
    "message": "Human-readable description",
    "details": { "field": "...optional context..." }
  }
}
```

Standard HTTP status codes:
- `400` — bad request (validation failure)
- `401` — missing or invalid JWT
- `403` — JWT valid but insufficient role
- `404` — resource not found
- `409` — state conflict (e.g. position already exists)
- `422` — pydantic validation failure
- `429` — rate limited
- `500` — internal error
- `503` — engine unavailable (e.g. Order Exec down)

## 3. Authentication Endpoints

### 3.1 `POST /auth/login`

Single-user login. Validates against `users` table; issues JWT.

Request:
```json
{
  "username": "admin",
  "password": "<plaintext>"
}
```

Response 200:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_at": "2026-04-29T09:15:00+05:30",
  "user": {
    "id": "uuid",
    "username": "admin",
    "role": "admin"
  }
}
```

Response 401: `{"error": {"code": "INVALID_CREDENTIALS", "message": "..."}}`

### 3.2 `POST /auth/refresh`

Exchanges a valid (non-expired) JWT for a new one with extended expiry.

Request: empty body, JWT in header.

Response 200: same shape as `/auth/login`.

### 3.3 `POST /auth/upstox-webhook`

Notifier webhook for Upstox v3 access-token-request flow. Receives the access token after user approval. **Public endpoint, no JWT required**, but validates the `client_id` field against the registered application.

Request (from Upstox):
```json
{
  "client_id": "...",
  "user_id": "...",
  "access_token": "...",
  "token_type": "Bearer",
  "expires_at": "1731448800000",
  "issued_at": "1731412800000",
  "message_type": "access_token"
}
```

Behavior:
- Validate `client_id` matches `user:credentials:upstox.api_key`
- Persist token to `user:auth:access_token` (Redis) and `user_credentials.encrypted_value.access_token` (Postgres, encrypted)
- Set `system:health:auth = valid`
- Publish `system:pub:system_event` with `{"event": "auth_refreshed"}`

Response 200: `{"ok": true}`
Response 400: `{"error": {"code": "INVALID_WEBHOOK_PAYLOAD", "message": "..."}}`

### 3.4 `GET /credentials/upstox`

Returns the masked Upstox credential bundle for the Settings UI. Secrets are partially masked (last 4 chars only) so the user can verify which values are stored without ever exposing the plaintext.

Response 200:
```json
{
  "configured": true,
  "auth_status": "valid",
  "api_key":         "****1a2b",
  "api_secret":      "****",
  "redirect_uri":    "https://bot.example.com/api/auth/upstox-webhook",
  "totp_secret":     "****",
  "mobile_no":       "******7890",
  "pin":             "****",
  "analytics_token": "****9f3e",
  "sandbox_token":   null,
  "access_token": {
    "present":    true,
    "expires_at": "2026-04-29T08:30:00+05:30",
    "source":     "webhook_v3"
  }
}
```

`auth_status` mirrors `system:health:auth` and is one of `valid`, `invalid`, `missing`, `unknown`.

### 3.5 `POST /credentials/upstox`

Sets or replaces the Upstox credential bundle. Encrypts with `CREDS_ENCRYPTION_KEY` (AES-256-GCM) and writes to `user_credentials` (Postgres) + `user:credentials:upstox` (Redis).

Request:
```json
{
  "api_key":         "abc123...",
  "api_secret":      "xyz789...",
  "redirect_uri":    "https://bot.example.com/api/auth/upstox-webhook",
  "totp_secret":     "JBSWY3DPEHPK3PXP",
  "mobile_no":       "9876547890",
  "pin":             "123456",
  "analytics_token": "...",          // optional
  "sandbox_token":   null            // optional
}
```

Behavior on success:
1. Encrypt and persist (Postgres `user_credentials.encrypted_value` and Redis `user:credentials:upstox`).
2. Probe `/v2/user/profile` synchronously using `analytics_token` if provided, else the cached `access_token` if any.
3. If probe 200:
   - `system:health:auth = valid`
   - Clear `system:flags:trading_disabled_reason` (set to `none`) **only if** the previous reason was `awaiting_credentials` or `auth_invalid`.
   - Set `system:flags:trading_active = true` if no other halt reason is active.
   - Publish `system:pub:system_event {"event": "auth_recovered"}`.
4. If probe non-200:
   - `system:health:auth = invalid`
   - `system:flags:trading_disabled_reason = auth_invalid`
   - Credentials are still saved (so the user can issue an access-token request next).

Response 200 (probe success):
```json
{
  "ok":          true,
  "auth_status": "valid",
  "trading_active":          true,
  "trading_disabled_reason": "none",
  "profile": { "user_id": "...", "user_name": "..." }
}
```

Response 200 (probe failure — saved but unverified):
```json
{
  "ok":          true,
  "auth_status": "invalid",
  "trading_active":          false,
  "trading_disabled_reason": "auth_invalid",
  "broker_error": { "code": "UDAPI100050", "message": "Invalid credentials" }
}
```

Response 422: pydantic validation failure (missing required field, malformed TOTP seed, etc.).

### 3.6 `DELETE /credentials/upstox`

Wipes all stored Upstox creds (Postgres row + Redis key + cached access token). Sets `system:health:auth = missing`, `trading_disabled_reason = awaiting_credentials`, `trading_active = false`. Used for full re-onboarding.

Response 200: `{"ok": true, "auth_status": "missing"}`

---

## 4. Configuration Endpoints

### 4.1 `GET /configs`

Returns all current configs (read from Redis `config:*`).

Response 200:
```json
{
  "execution":  { ... },
  "session":    { ... },
  "risk":       { ... },
  "indexes": {
    "nifty50":   { ... },
    "banknifty": { ... }
  }
}
```

### 4.2 `GET /configs/{section}`

Returns one config section. `section` is one of `execution`, `session`, `risk`, `index:nifty50`, `index:banknifty`.

Response 200: the config JSON for that section.

### 4.3 `PUT /configs/{section}`

Write-through update. Validates against pydantic schema for the section. Atomically writes to Postgres (`configs.value`) and Redis (`config:{section}`) via Lua script.

Request: complete config object for that section (full replace, not merge).

Response 200:
```json
{
  "ok": true,
  "section": "execution",
  "updated_at": "2026-04-28T10:00:00+05:30",
  "value": { ... }
}
```

Response 422: pydantic validation errors with field-level detail.

---

## 5. Strategy Control

### 5.1 `GET /strategy/status`

Returns enabled state and current trading state for all three indexes.

Response 200:
```json
{
  "system": {
    "trading_active": true,
    "mode": "live",
    "daily_loss_circuit_triggered": false
  },
  "indexes": {
    "nifty50":   { "enabled": true, "state": "IN_CE", "current_position_id": "..." },
    "banknifty": { "enabled": true, "state": "FLAT", "current_position_id": null }
  }
}
```

### 5.2 `POST /commands/halt_index/{index}`

Disables an index's strategy thread (sets `strategy:{index}:enabled = false`). Existing position on that index is NOT auto-closed; it follows its normal exit rules. New entries on that index are blocked.

Path param: `index` ∈ {nifty50, banknifty}.

Response 200: `{"ok": true, "index": "...", "enabled": false}`

### 5.3 `POST /commands/resume_index/{index}`

Re-enables an index's strategy thread.

Response 200: `{"ok": true, "index": "...", "enabled": true}`

### 5.4 `POST /commands/global_kill`

Emergency kill: sets `system:flags:trading_active = false`, force-exits all open positions immediately via `XADD system:stream:control {"event": "global_kill"}`.

Request body:
```json
{
  "reason": "manual operator intervention"
}
```

Response 200: `{"ok": true, "exiting_positions": 2, "halted_at": "..."}`

### 5.5 `POST /commands/global_resume`

Re-enables system trading after a global kill. Resets `system:flags:trading_active = true` and `system:flags:daily_loss_circuit_triggered = false`. Requires explicit reason.

Request:
```json
{
  "reason": "issue resolved",
  "reset_daily_loss_circuit": true
}
```

Response 200: `{"ok": true, "trading_active": true}`

---

## 6. Position Endpoints

### 6.1 `GET /positions/open`

Returns all currently-open positions.

Response 200:
```json
{
  "items": [
    {
      "pos_id": "...",
      "index": "nifty50",
      "side": "CE",
      "strike": 24500,
      "instrument_token": "NSE_FO|49520",
      "qty": 75,
      "entry_price": 142.5,
      "entry_ts": "2026-04-28T09:25:32+05:30",
      "current_premium": 158.0,
      "pnl": 1162.5,
      "pnl_pct": 10.88,
      "holding_seconds": 305,
      "sl_level": 114.0,
      "target_level": 213.75,
      "tsl_armed": false,
      "tsl_level": null,
      "exit_profile": { ... },
      "status_stage": "EXIT_EVAL"
    }
  ]
}
```

### 6.2 `GET /positions/closed_today`

Returns all positions closed today, sorted by `exit_ts` descending. Each item includes the full closed-position payload (everything in `trades_closed_positions` row).

Response 200:
```json
{
  "items": [
    { ...full trades_closed_positions row JSON... }
  ]
}
```

### 6.3 `GET /positions/history`

Paged historical query against Postgres `trades_closed_positions`.

Query params:
- `index` (optional): filter by index
- `mode` (optional): `paper` or `live`
- `from` (required): ISO date (e.g. `2026-04-01`)
- `to` (required): ISO date (e.g. `2026-04-28`)
- `page` (default 1)
- `page_size` (default 50, max 200)
- `sort` (default `entry_ts_desc`): `entry_ts_asc` | `entry_ts_desc` | `pnl_desc` | `pnl_asc`

Response 200:
```json
{
  "items": [ ... ],
  "page": 1,
  "page_size": 50,
  "total": 1234,
  "total_pages": 25
}
```

### 6.4 `GET /reports/{position_id}`

Returns the full closed-position report (every JSONB column expanded).

Response 200: complete `trades_closed_positions` row.
Response 404: `{"error": {"code": "POSITION_NOT_FOUND", ...}}`

### 6.5 `POST /commands/manual_exit/{position_id}`

Manually triggers exit on an open position. Publishes to `orders:stream:manual_exit`.

Path param: `position_id`.

Request body:
```json
{
  "reason": "manual operator decision"
}
```

Response 200: `{"ok": true, "queued": true, "position_id": "..."}`
Response 404: position not open or not found.

---

## 7. PnL Endpoints

### 7.1 `GET /pnl/live`

Returns the current PnL view (read from Redis `view:pnl`).

Response 200:
```json
{
  "realized_today": 4250.0,
  "unrealized": 1162.5,
  "total_today": 5412.5,
  "total_today_pct_of_capital": 2.71,
  "trades_today": 7,
  "wins_today": 5,
  "win_rate": 0.714,
  "per_index": {
    "nifty50":   { "realized": 3000, "unrealized": 1162.5, "trades": 4, "win_rate": 0.75 },
    "banknifty": { "realized": 1250, "unrealized": 0,      "trades": 3, "win_rate": 0.66 }
  },
  "ts": "2026-04-28T11:42:30+05:30"
}
```

### 7.2 `GET /pnl/history`

Time-series PnL data from `metrics_pnl_history` table.

Query params:
- `index` (optional): filter by index, omit for combined
- `from` (required): ISO date
- `to` (required): ISO date
- `granularity` (default `1d`): `1m` | `5m` | `15m` | `1h` | `1d`

Response 200:
```json
{
  "series": [
    { "ts": "2026-04-01", "realized": 5400, "unrealized": 0, "open_count": 0, "day_trades": 8 },
    { "ts": "2026-04-02", "realized": -1200, "unrealized": 0, "open_count": 0, "day_trades": 6 }
  ]
}
```

---

## 8. ΔPCR Endpoints

### 8.1 `GET /delta_pcr/{index}/live`

Returns the current ΔPCR view for an index.

Response 200:
```json
{
  "index": "nifty50",
  "interval": {
    "ts": "2026-04-28T11:42:00+05:30",
    "atm": 24500,
    "interval_pcr": 1.34,
    "total_d_put_oi": 125000,
    "total_d_call_oi": 93000
  },
  "cumulative": {
    "ts": "2026-04-28T11:42:00+05:30",
    "cumulative_pcr": 1.18,
    "cumulative_d_put_oi": 480000,
    "cumulative_d_call_oi": 405000
  },
  "history": [ ... last 20 intervals ... ],
  "interpretation": "BULLISH",
  "mismatch_flag": false,
  "mode": 1
}
```

### 8.2 `GET /delta_pcr/{index}/history`

Time-series query from Postgres `metrics_delta_pcr_history`.

Query params: `from`, `to`.

Response 200: list of `metrics_delta_pcr_history` rows.

### 8.3 `PUT /delta_pcr/{index}/mode`

Updates ΔPCR operating mode for an index (1=display, 2=soft veto, 3=hard veto).

Request:
```json
{ "mode": 2 }
```

Response 200: `{"ok": true, "index": "...", "mode": 2}`

---

## 9. Health Endpoints

### 9.1 `GET /health`

Public endpoint (no auth). Returns the current `view:health`.

Response 200:
```json
{
  "summary": "OK",
  "engines": {
    "init":              { "alive": true, "last_hb_ts": "..." },
    "data_pipeline":     { "alive": true, "last_hb_ts": "..." },
    "strategy:nifty50":  { "alive": true, "last_hb_ts": "..." },
    "strategy:banknifty":{ "alive": true, "last_hb_ts": "..." },
    "order_exec":        { "alive": true, "last_hb_ts": "..." },
    "background:position_ws":           { "alive": true, "last_hb_ts": "..." },
    "background:pnl":                   { "alive": true, "last_hb_ts": "..." },
    "background:delta_pcr:nifty50":     { "alive": true, "last_hb_ts": "..." },
    "background:delta_pcr:banknifty":   { "alive": true, "last_hb_ts": "..." },
    "scheduler":         { "alive": true, "last_hb_ts": "..." },
    "health":            { "alive": true, "last_hb_ts": "..." },
    "api_gateway":       { "alive": true, "last_hb_ts": "..." }
  },
  "dependencies": {
    "redis":                 "OK",
    "postgres":              "OK",
    "broker_market_ws":      "OK",
    "broker_portfolio_ws":   "OK",
    "broker_rest":           "OK",
    "auth":                  "valid"
  },
  "alerts": [
    { "ts": "...", "level": "WARNING", "msg": "broker WS reconnected after 12s outage" }
  ],
  "ts": "2026-04-28T11:42:30+05:30"
}
```

### 9.2 `GET /health/dependencies/test`

Forces a fresh probe of each dependency (Redis PING, Postgres SELECT 1, broker auth, kill switch). Returns probe results synchronously.

Response 200:
```json
{
  "redis":     { "ok": true,  "latency_ms": 0.4 },
  "postgres":  { "ok": true,  "latency_ms": 2.1 },
  "broker":    { "ok": true,  "latency_ms": 142, "auth_valid": true, "kill_switch_clear": true }
}
```

---

## 10. Operational Endpoints

### 10.1 `POST /commands/instrument_refresh`

Forces immediate broker instruments file refresh and re-detection of current expiries per index.

Response 200:
```json
{
  "ok": true,
  "indexes": {
    "nifty50":   { "expiry": "NIFTY24W25", "tokens_loaded": 1234 },
    "banknifty": { "expiry": "BANKNIFTY24M28", "tokens_loaded": 678 }
  }
}
```

### 10.2 `POST /commands/upstox_token_request`

Triggers the Upstox v3 access-token-request flow (sends approval prompt to user's Upstox app + WhatsApp).

Response 200:
```json
{
  "ok": true,
  "authorization_expiry": "1731448800000",
  "notifier_url": "...",
  "message": "Approve the request in your Upstox app or WhatsApp; the token will arrive at the notifier webhook."
}
```

### 10.3 `GET /capital/funds`

Returns current capital snapshot (read from Redis `user:capital:funds`).

Response 200:
```json
{
  "available_to_trade":   { ...full v3 funds-and-margin payload... },
  "unavailable_to_trade": { ... },
  "ts": "2026-04-28T11:30:00+05:30"
}
```

### 10.4 `GET /capital/kill_switch`

Returns broker's per-segment kill switch status (read from Redis `user:capital:kill_switch`).

Response 200:
```json
{
  "segments": [
    { "segment": "NSE_FO", "segment_status": "ACTIVE", "kill_switch_enabled": false },
    ...
  ],
  "ts": "..."
}
```

### 10.5 `POST /capital/kill_switch`

Triggers Upstox kill switch toggle.

Request:
```json
{
  "toggles": [
    { "segment": "NSE_FO", "action": "DISABLE" }
  ]
}
```

Response 200: updated full status snapshot.

---

## 10b. Analytics Endpoints (Phase 10b)

These endpoints back the `/analytics` page (`docs/frontend/06_Charts_Analytics.md`).
They land together with the new tables specified in `docs/Schema.md` Phase
10b section. **They are not implemented in Phase 9** — Phase 9 ships
without these.

All three endpoints require an admin JWT.

### 10b.1 `GET /analytics/option_chain/{index}`

Returns time-bucketed option-chain rollups for a given index.

Path:
- `index`: `nifty50` | `banknifty`

Query:
- `from`: ISO date (required, e.g. `2026-04-28`)
- `to`: ISO date (required)
- `granularity`: `1m` | `5m` | `15m` | `1h` (required)
- `metrics`: comma-separated subset of `pcr,oi_change,multi_strike_oi,max_pain,oi_total,premium_diff` (default: all)
- `strikes`: comma-separated integers (optional, only used by
  `multi_strike_oi`; defaults to ATM ±2)

Response 200:
```json
{
  "index": "nifty50",
  "granularity": "5m",
  "from": "2026-04-28",
  "to": "2026-04-28",
  "series": [
    {
      "ts": "2026-04-28T09:15:00+05:30",
      "atm": 24500,
      "call_oi": 12450000,
      "put_oi": 14580000,
      "pcr": 1.17,
      "max_pain": 24500,
      "premium_diff": { "ce_atm": 1.5, "pe_atm": -0.8 },
      "strike_oi": { "24450": 1420000, "24500": 1750000, "24550": 1300000 }
    }
  ]
}
```

Errors:
- `404` `INDEX_NOT_FOUND` — unknown index
- `400` `INVALID_GRANULARITY` — granularity not in allowed set
- `400` `RANGE_TOO_LARGE` — server may cap (`from`–`to`) span depending on
  granularity (1m: max 7 days; 5m: 30 days; 15m: 90 days; 1h: 365 days)

### 10b.2 `GET /analytics/snapshots/{index}`

Returns the marker snapshots captured for a given trading day.

Path:
- `index`: `nifty50` | `banknifty`

Query:
- `date`: ISO date (required, e.g. `2026-04-28`)
- `kind`: optional filter (`pre_open` | `market_open` | `mid_session_1..4` |
  `pre_close` | `eod`)

Response 200:
```json
{
  "index": "nifty50",
  "date": "2026-04-28",
  "items": [
    {
      "ts": "2026-04-28T09:14:00+05:30",
      "kind": "pre_open",
      "payload": { "atm": 24500, "ce_premiums": { "24450": 142.5 }, "pe_premiums": { "24450": 95.0 } }
    },
    {
      "ts": "2026-04-28T15:35:00+05:30",
      "kind": "eod",
      "payload": { "realized_pnl": 5412.5, "trades": 7, "win_rate": 0.71 }
    }
  ]
}
```

### 10b.3 `GET /analytics/strategy/{index}`

Returns aggregate strategy stats for the date range plus a heatmap.

Path:
- `index`: `nifty50` | `banknifty`

Query:
- `from`: ISO date (required)
- `to`: ISO date (required)

Response 200:
```json
{
  "index": "nifty50",
  "from": "2026-04-01",
  "to": "2026-04-30",
  "summary": {
    "entries": 142,
    "win_rate": 0.62,
    "avg_pnl": 1845.0,
    "reversal_rate": 0.21,
    "total_pnl": 261990.0
  },
  "heatmap": [
    { "weekday": 1, "bucket_hhmm": "09:15", "count": 7,  "avg_pnl": 1450.0, "win_rate": 0.71 },
    { "weekday": 1, "bucket_hhmm": "09:30", "count": 9,  "avg_pnl":  980.0, "win_rate": 0.55 },
    { "weekday": 5, "bucket_hhmm": "15:15", "count": 12, "avg_pnl": -240.0, "win_rate": 0.42 }
  ]
}
```

`weekday` is 1 (Mon) – 5 (Fri). Bucket size is 15 min.

---

## 11. WebSocket Protocol

### 11.1 Endpoint

`WS /stream?token=<JWT>`

Single always-on connection per logged-in client. Multiplexes all view subscriptions.

### 11.2 Connection Sequence

**Client → Server (after WS handshake)**:
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

**Server → Client (initial snapshot)**:
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

### 11.3 Update Protocol

When any subscribed view changes, server sends:

```json
{
  "type": "update",
  "ts": "2026-04-28T11:42:31+05:30",
  "view": "position:nifty50",
  "data": { ... full new view payload ... }
}
```

**Full-replacement only**, never partial-diff. Client overwrites `store["position:nifty50"]` wholesale.

### 11.4 Heartbeat

Server sends every 20 seconds:
```json
{ "type": "ping", "ts": "..." }
```

Client must reply within 20 seconds:
```json
{ "type": "pong" }
```

If no pong received within 30s, server closes connection. Client reconnects with the same subscribe message.

### 11.5 Server-Initiated Notifications (push, not view updates)

```json
{
  "type": "notification",
  "ts": "...",
  "level": "WARNING",
  "msg": "Broker portfolio WS disconnected; reconnecting"
}
```

```json
{
  "type": "notification",
  "ts": "...",
  "level": "CRITICAL",
  "msg": "Daily loss circuit triggered; all positions exiting"
}
```

### 11.6 Subscription Mutation Mid-Connection

Client can change subscriptions without reconnecting:

```json
{
  "type": "subscribe",
  "views": [ ... new full list ... ]
}
```

Server replaces subscription set, sends fresh snapshot of any newly-subscribed view, stops sending updates for unsubscribed views.

### 11.7 Close Codes

| Code | Reason |
|---|---|
| 1000 | Normal close (server shutdown) |
| 1008 | Auth invalid (JWT expired or invalid) |
| 1011 | Server internal error |
| 4000 | Heartbeat timeout |
| 4001 | Subscription validation failed |

---

## 12. View Payload Shapes

### 12.1 `view:dashboard`
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

### 12.2 `view:position:{index}`
Either `null` (no open position for that index) or the full position object as in §6.1 items.

### 12.3 `view:strategy:{index}`
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

### 12.4 `view:delta_pcr:{index}`
See §8.1.

### 12.5 `view:pnl`
See §7.1.

### 12.6 `view:capital`
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

### 12.7 `view:health`
See §9.1.

### 12.8 `view:positions:closed_today`
```json
{
  "items": [
    { ...full trades_closed_positions row... }
  ],
  "count": 7,
  "ts": "..."
}
```

### 12.9 `view:configs`
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

## 13. Rate Limits

Per-IP rate limits enforced at the gateway:
- `POST /auth/login`: 5 requests / minute
- `POST /commands/*`: 10 requests / minute
- All other endpoints: 60 requests / minute
- WebSocket: 1 active connection per JWT (older connection closed on new connect)

Exceeded → `429 Too Many Requests` with `Retry-After` header.

---

## 14. CORS

In development: `Access-Control-Allow-Origin: http://localhost:3000` (Next.js dev server).
In production: configurable via `config:api.cors_origins`; default is the frontend's own origin.

---

## 15. Module Layout (`engines/api_gateway/`)

```
api_gateway/
├── main.py                    # FastAPI app, lifespan, middleware
├── auth.py                    # JWT issuance + verification
├── deps.py                    # FastAPI dependencies (get_current_user, get_redis, get_postgres)
├── ws_endpoints.py            # /stream WebSocket handler
├── view_router.py             # pub:view → WS push fanout
├── rest/
│   ├── auth.py                # /auth/*
│   ├── credentials.py         # /credentials/upstox (GET/POST/DELETE)
│   ├── configs.py             # /configs/*
│   ├── strategy.py            # /strategy/*, /commands/halt_index, /commands/resume_index
│   ├── positions.py           # /positions/*, /reports/*
│   ├── pnl.py                 # /pnl/*
│   ├── delta_pcr.py           # /delta_pcr/*
│   ├── health.py              # /health, /health/dependencies/test
│   ├── capital.py             # /capital/*
│   ├── commands.py            # /commands/*
│   └── webhooks/
│       └── upstox_token.py    # /auth/upstox-webhook
└── middleware/
    ├── jwt_middleware.py
    ├── rate_limit_middleware.py
    └── error_handler.py
```

Each REST module exposes a single `router: APIRouter` that `main.py` mounts. No business logic in `main.py` — only app construction, middleware wiring, and lifespan handlers.
