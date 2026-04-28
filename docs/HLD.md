# High-Level Design — Premium-Diff Multi-Index Trading Bot

## 1. Design Goals

1. Sub-50ms internal hop budget from tick arrival to order submission decision
2. Two independent index strategy instances (NIFTY 50, BANKNIFTY) sharing one market data feed and one execution layer
3. Single source of truth: Redis for hot runtime state, PostgreSQL for durable state
4. Single-writer rule per Redis key namespace
5. Process-isolated engines — restart any engine without disturbing others
6. 24×7 backend lifecycle with daily 04:00 IST controlled restart for memory hygiene
7. Push-only frontend protocol — server-owned canonical view keys, atomic full-view replacement
8. Fully debuggable closed positions — every trade carries its full forensic record into PostgreSQL
9. Single user (no multi-tenancy in this version)
10. Simple stack: Redis + Postgres + Python backend + web frontend. No external monitoring infra.

## 2. Topology

```
                ┌─────────────────────────────────────────────────────────────┐
                │                     EC2 (single host, 24×7)                 │
                │                                                             │
   Browser ─WSS─▶ FastAPI Gateway ◀── ui:pub:view ── Redis ◀── all engines    │
   (web UI)          │                                  ▲                     │
                     ▼                                  │                     │
              ┌──────────────────────────────────────────┴──────────────────┐ │
              │                          Redis 7                            │ │
              │  6 namespaces · streams · pub/sub · view keys · runtime cfg │ │
              └──────────────────────────────────────────────────────────────┘ │
                     ▲                ▲              ▲             ▲           │
                     │                │              │             │           │
              ┌──────┴───────┐ ┌──────┴───────┐ ┌────┴─────────┐ ┌─┴───────┐   │
              │ Init Engine  │ │ Data         │ │ Strategy     │ │ Order   │   │
              │  (one-shot)  │ │ Pipeline     │ │ Engine       │ │ Exec    │   │
              │              │ │ (Upstox WS)  │ │ (2 threads:  │ │ (thread │   │
              │              │ │              │ │  NIFTY,      │ │  pool)  │   │
              │              │ │              │ │  BANKNIFTY)  │ │         │   │
              └──────────────┘ └──────────────┘ └──────────────┘ └─────────┘   │
                                       │                              │        │
                                       ▼                              ▼        │
                              broker market WS              broker order REST  │
                                                            broker portfolio WS│
                                                                               │
              ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
              │ Background   │ │ Scheduler    │ │ Health       │               │
              │ Engine       │ │              │ │              │               │
              │ (2 ΔPCR +    │ │              │ │              │               │
              │  PnL + WS)   │ │              │ │              │               │
              └──────────────┘ └──────────────┘ └──────────────┘               │
                     │                                                         │
                     ▼                                                         │
              ┌──────────────────────────────────────────────────────────┐     │
              │              PostgreSQL 16                               │     │
              │   user_* · config_* · market_* · trades_* · metrics_*    │     │
              └──────────────────────────────────────────────────────────┘     │
              └─────────────────────────────────────────────────────────────────┘
```

## 3. Tech Stack (intentionally minimal)

| Layer | Choice |
|---|---|
| Engine runtime | Python 3.12 + `uvloop` + `asyncio` |
| Process model | `multiprocessing.Process` per engine, supervised by `systemd` |
| Hot state / bus | Redis 7 (Unix socket, AOF `everysec`, Streams + Pub/Sub + Hashes) |
| Cold store | PostgreSQL 16 (no extensions required) |
| API gateway | FastAPI + `websockets` + `orjson` |
| Broker | Upstox REST + Market Data WS (protobuf v3) + Portfolio Order WS |
| Serialization | `orjson` everywhere on hot path |
| Supervision | systemd units, one per engine, ordered dependencies |
| Logging | `loguru` → stdout → `systemd journald` (queryable via `journalctl`) |
| Latency metrics | embedded in `trades_closed_positions.latencies` JSONB at trade close; ad-hoc analysis via SQL |
| Frontend | Next.js (web) — built later, not in this phase |
| Auth | JWT (single-user; bcrypt password in Postgres) |

**Explicitly not used (kept simple):** Prometheus, Grafana, Loki, Promtail, OpenTelemetry, Jaeger/Tempo, TimescaleDB, Kafka, ECS, Kubernetes.

## 4. Engines

Each engine is one OS process (systemd unit) with one asyncio loop (`uvloop`). Threads are used only for blocking I/O or for per-index parallelization within an engine.

### 4.1 Init Engine
One-shot. Runs at boot, exits when done. Owns the canonical Redis schema template (`init_engine/redis_template.py`) and the daily bootstrap sequence.

Boot sequence:
1. Connect Redis (Unix socket) + Postgres
2. `FLUSHDB` scoped to runtime namespaces (preserve `user:*`, `config:*` mirrors)
3. Walk canonical schema template; write defaults for every key
4. Hydrate `user:*` and `strategy:configs:*` from Postgres
5. **Holiday check** — query `system:scheduler:market_calendar:trading_days` for today; if non-trading, set `system:flags:trading_active = "false"`, exit clean
6. **Auth bootstrap** — call `auth.is_token_valid_remote()`; if invalid, run Playwright login or block until v3 token-request webhook delivers; persist to `user:auth:access_token`
7. **Instrument fetch per index** — for each enabled index:
   - Fetch spot LTP + prev close via `market_quote.get_full_quote()`
   - Compute ATM = `round(spot / strike_step) × strike_step`
   - Fetch option contracts via `option_contract.fetch()` (no expiry filter)
   - Discover nearest expiry: `min(expiry where expiry >= today)`
   - Filter to ATM ± 6 strikes for that expiry
   - Build `market_data:indexes:{index}:meta` (lot_size, expiry, atm_at_open, prev_close, ...)
   - Build `market_data:indexes:{index}:option_chain` (per-strike CE/PE template with empty WS placeholder fields)
   - Build `strategy:{index}:basket` (locked ATM ± 2 trading set)
   - Add all 26 option tokens + 1 spot token to `market_data:subscriptions:desired`
8. Set `system:flags:ready = "true"`
9. Publish `system:pub:system_event {event: ready}`
10. Exit 0

On any exception → `system:flags:init_failed = <reason>`, exit 1, systemd halts dependents.

### 4.2 Data Pipeline Engine
Owns the broker market WebSocket. Threads:
- `T_io` — broker WS IO (decodes protobuf → asyncio queue); reconnect with exponential backoff
- `T_processor` — drains queue, writes ticks directly into `market_data:indexes:{index}:option_chain.{strike}.{ce|pe}` placeholder fields
- `T_subscription_manager` — maintains dynamic ATM ± 6 window per index; subscribes new edge strikes when ATM shifts, unsubscribes ones too far out

WebSocket subscription is built **at 09:14:00** (pre-market) so quotes are flowing before pre-open snapshot at 09:14:50.

Heartbeat: writes ts to `system:health:heartbeats` field `data_pipeline` every 1s (TTL 5s).

### 4.3 Strategy Engine
Two independent strategy threads, one per index, hosted in the same process.

- `T_strategy_nifty50` — independent state machine for NIFTY 50
- `T_strategy_banknifty` — independent state machine for BANKNIFTY

Each thread:
1. At 09:14:50 — capture pre-open snapshot for its 6-strike basket; write to `strategy:{index}:pre_open`. Fail-closed if any basket strike has zero ts (per-index opt-out for the day).
2. 09:15:00 → 09:15:09 — settle window: read ticks but emit no signals (filters auction-crossover noise).
3. From 09:15:10 — continuous decision loop on each tick. State machine: `FLAT` / `IN_CE` / `IN_PE` / `COOLDOWN` / `HALTED` (Strategy.md §3).
4. Compute `SUM_CE`, `SUM_PE`, `delta = SUM_PE − SUM_CE` per tick; write to `strategy:{index}:live:*`.
5. Emit entry signals when `FLAT`, reversal flip signals when `IN_*`, no signals when `COOLDOWN` or `HALTED`.
6. Honor daily caps (`max_entries_per_day` / `max_reversals_per_day`) and the entry-freeze window (15:10 → 15:15).
7. Read ΔPCR from `strategy:{index}:delta_pcr:cumulative` for veto modes 2/3 if enabled (default: mode 1 — informational only).

Signals emitted to `strategy:stream:signals` with `index` tag, idempotent on `sig_id = sha256(index | tick_seq | side | strike | intent)`.

Heartbeat: per-index field in `system:health:heartbeats` — `strategy:nifty50`, `strategy:banknifty`.

### 4.4 Order Execution Engine
Single shared process serving both indexes via a pre-warmed thread pool.

- Pre-warmed thread pool: configurable size (default 8)
- Dispatcher coroutine consumes `strategy:stream:signals` consumer group, hands signal to next free worker thread
- Each worker runs the canonical lifecycle:
  - Pre-entry gate (Redis reads only)
  - Branch by mode (paper or live)
  - Entry submission (DAY limit + monitor + modify/cancel)
  - Exit monitoring (event-driven on tick stream + manual exit + composite exit pressure)
  - Exit submission (DAY limit + modify-only loop)
  - Report assembly + Postgres insert + atomic Redis cleanup (Lua)
- Workers route per-index config (lot size, thresholds) by reading `index` tag from signal
- Per-index concurrency: 1 max (enforced by allocator); total: 2 max

Heartbeat: `system:health:heartbeats` field `order_exec`.

### 4.5 Background Engine
Threads:
- `T_position_ws` — broker portfolio WebSocket; writes `orders:broker:pos:{order_id}` HASH on every fill/order event + `XADD orders:stream:order_events`
- `T_pnl` — every 1s, reads open positions + ticks → updates `orders:pnl:realized`, `orders:pnl:unrealized`, `orders:pnl:per_index:{index}`, `orders:pnl:day`
- `T_delta_pcr_nifty50` — every 3 min from 09:18, computes ΔPCR for NIFTY 50, writes `strategy:nifty50:delta_pcr:*`
- `T_delta_pcr_banknifty` — same for BANKNIFTY
- `T_token_refresh` — checks `auth.is_token_valid_remote()` every 5 min
- `T_capital_poll` — `capital.get_capital()` every 30s → `user:capital:funds`
- `T_kill_switch_poll` — `kill_switch.get_kill_switch_status()` every 30s → `user:capital:kill_switch`

Heartbeat: per-thread fields under `system:health:heartbeats`.

### 4.6 Scheduler Engine
Reads task definitions from `system:scheduler:tasks`. Each task: cron / event_to_publish / target_engines. Fires `system:stream:control` events: `pre_open_snapshot`, `session_open`, `eod_squareoff`, `session_close`, `graceful_shutdown`, `instrument_refresh`, `token_refresh_check`.

Heartbeat: `system:health:heartbeats` field `scheduler`.

### 4.7 Health Engine
- Watches every field in `system:health:heartbeats` HASH for staleness
- Probes Redis (`PING`), Postgres (`SELECT 1`), broker reachability, `auth.is_token_valid_remote()`, kill switch
- Writes `system:health:engines`, `system:health:dependencies`, `system:health:summary`
- Rebuilds `ui:views:health` on any change

Heartbeat: `system:health:heartbeats` field `health`.

### 4.8 FastAPI Gateway
- `WS /stream` — auth via JWT; client subscribes to view names; gateway pushes initial snapshot then deltas via `ui:pub:view`
- `POST /auth/login` — single-user JWT issuance
- `POST /auth/upstox-webhook` — Notifier Webhook for v3 `request_access_token` flow
- REST `/configs/*` — write-through (Postgres + Redis atomic via Lua)
- REST `/positions/history?from&to&index&page` — Postgres `trades_closed_positions` paged read
- REST `/reports/{position_id}` — full debuggable JSON
- `POST /commands/manual_exit/{position_id}` → `XADD orders:stream:manual_exit`
- `POST /commands/halt_index/{index}` → write `strategy:{index}:enabled = false`
- `POST /commands/global_kill` → write `system:flags:trading_active = false`, force-exit all open

No business logic. Pure pipe. Full spec in `API.md`.

## 5. Per-Index Parallelization Model

| Engine | Per-Index Pattern |
|---|---|
| Data Pipeline | Single thread; both indexes' ticks flow through the same WS connection |
| Strategy Engine | **2 threads** — one per index, completely independent state machines |
| Order Execution | Shared thread pool; signals tagged with `index` are routed to any free worker |
| Background Engine | **2 ΔPCR threads** + 1 position WS thread + 1 PnL thread + util threads |
| Scheduler | Single thread; events fired with `index` payload when relevant |
| Health | Single thread; tracks both indexes' strategy heartbeats separately |

## 6. Streams & Pub/Sub Channels

```
system:stream:control               Scheduler   →  all engines
market_data:stream:tick:nifty50     DataPipe    →  Strategy.NIFTY, Order Exec
market_data:stream:tick:banknifty   DataPipe    →  Strategy.BANKNIFTY, Order Exec
strategy:stream:signals             Strategy    →  Order Exec (consumer group "exec")
strategy:stream:rejected_signals    Strategy    →  audit only
orders:stream:order_events          Background  →  Order Exec, FastAPI
orders:stream:manual_exit           FastAPI     →  Order Exec
ui:stream:health_alerts             Health      →  FastAPI

ui:pub:view                         (any)       →  FastAPI  (payload = view key name)
system:pub:system_event             (any)       →  all      (system:ready, shutdown)
```

## 7. Frontend Contract (push-only)

Every screen has exactly one Redis "view key" under `ui:views:*` that holds the complete, ready-to-render JSON for that screen. The owner engine rebuilds the view atomically when underlying state changes and publishes the key name to `ui:pub:view`. FastAPI fans out to all subscribed clients.

| Screen | View Key | Owner |
|---|---|---|
| Dashboard | `ui:views:dashboard` | Background |
| Live position (per index) | `ui:views:position:{index}` | Order Exec |
| Closed today | `ui:views:positions_closed_today` | Order Exec |
| Strategy state per index | `ui:views:strategy:{index}` | Strategy Engine |
| ΔPCR per index | `ui:views:delta_pcr:{index}` | Background |
| Health | `ui:views:health` | Health |
| PnL | `ui:views:pnl` | Background |
| Capital | `ui:views:capital` | Background |
| Configs | `ui:views:configs` | Init + FastAPI |

Historical data (`positions/history`, `reports/{id}`, PnL history) goes REST → Postgres on demand, never through view keys.

Frontend reducer: `ws.onmessage = msg => store[msg.view] = msg.data`. No client-side merging, no diffing, no optimistic state. Initial connect → batched snapshot of all subscribed views. Reconnect → fresh snapshot.

## 8. Daily Lifecycle

```
03:55  Scheduler fires graceful_shutdown event
       ├─ Order Exec drains in-flight signals
       ├─ Strategy stops consuming tick streams
       ├─ Data Pipeline closes broker WS
       ├─ Background closes portfolio WS, flushes pnl_history
       └─ FastAPI sends close frame to clients

04:00  systemd stops all engine units (Redis + Postgres remain up)

04:01  systemd starts Init Engine
       ├─ Connect Redis + Postgres
       ├─ FLUSHDB scoped to runtime namespaces
       ├─ Apply canonical Redis template
       ├─ Hydrate user:* and config:* from Postgres
       ├─ Holiday check
       ├─ Auth bootstrap (refresh token if needed)
       ├─ Instrument fetch + ATM compute + basket build per index
       ├─ Populate subscription:desired
       └─ system:flags:ready = true; PUBLISH system:pub:system_event

04:02  systemd starts engines in order:
       Data Pipeline → Background → Strategy → Order Exec
       → Scheduler → Health → FastAPI Gateway

04:05  Background runs auth refresh check
05:30  Pre-market: instrument file refresh, expiry validation
09:14:00  Data Pipeline subscribes broker WS (54 instruments total)
09:14:50  Strategy threads capture pre-open snapshots
          Background captures ΔPCR baseline OI per index
09:15:00  Market open  (settle window 09:15:00 → 09:15:09)
09:15:10  Continuous decision loop begins per index
09:18+    ΔPCR threads compute every 3 min
15:10  Entry freeze (no new entries; existing positions still managed)
15:15  EOD square-off
15:30  Market close + EOD report generation
15:45  Graceful shutdown initiated  (cycle repeats next weekday at 08:00)
```

PostgreSQL never restarted. Redis never restarted (just FLUSHDB-ed by Init).

## 9. Hot-Path Discipline

- No `time.sleep` on hot paths. Use `XREAD BLOCK 0`, `asyncio.Event`, or `asyncio.sleep(0)`
- `orjson` everywhere on hot path; never stdlib `json`
- Every multi-key Redis op uses `pipeline()` or Lua — single round-trip
- No `KEYS *` or unbounded `SCAN`; maintain index sets
- Engines pinned to CPU cores in systemd units (`CPUAffinity=`)
- Redis on Unix socket, never TCP localhost
- Single-writer rule enforced per Redis key prefix; readers unrestricted

## 10. Logging & Latency

**Logs**: `loguru` configured to emit structured JSON to stdout. systemd captures stdout per service into journald. Query via:

```
journalctl -u trading-strategy.service -f                    # tail live
journalctl -u trading-order-exec.service --since "09:15"     # since time
journalctl -u trading-order-exec.service | grep "$sig_id"   # by signal
```

`SystemMaxUse=2G` set in `/etc/systemd/journald.conf` to cap disk usage. Journald rotates automatically.

Every log line includes structured fields: `ts, level, engine, module, sig_id, pos_id, order_id, index, msg`.

**Latency**: captured per-trade as JSONB in `trades_closed_positions.latencies`:
```json
{
  "signal_to_submit_ms": 12,
  "submit_to_ack_ms": 78,
  "ack_to_fill_ms": 1240,
  "decision_to_exit_submit_ms": 4,
  "exit_submit_to_fill_ms": 890
}
```

For aggregate latency analysis, query Postgres directly:
```sql
SELECT index, mode,
       AVG((latencies->>'signal_to_submit_ms')::int) AS avg_signal_to_submit,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY (latencies->>'ack_to_fill_ms')::int) AS p95_ack_to_fill
FROM trades_closed_positions
WHERE entry_ts >= now() - interval '7 days'
GROUP BY index, mode;
```

No external monitoring infra. If/when latency analysis becomes a daily need, revisit.

## 11. Project Structure

```
premium_diff_bot/
├── backend/
│   ├── brokers/
│   │   └── upstox/                  # broker SDK; engines import via UpstoxAPI facade
│   │       ├── __init__.py          # re-exports UpstoxAPI
│   │       ├── client.py            # UpstoxAPI: 45 stateless classmethods
│   │       ├── auth.py              # OAuth2 v2 + v3 token request + token-validity probe
│   │       ├── profile.py           # /v2/user/profile
│   │       ├── capital.py           # /v3/user/get-funds-and-margin
│   │       ├── kill_switch.py       # /v2/user/kill-switch
│   │       ├── static_ips.py        # /v2/user/ip
│   │       ├── holidays.py          # /v2/market/holidays + is_holiday_for predicate
│   │       ├── market_timings.py    # /v2/market/timings/{date}
│   │       ├── market_status.py     # /v2/market/status/{exchange}
│   │       ├── market_data.py       # /v3/market-quote/ltp
│   │       ├── instruments.py       # CDN NSE.json.gz master contract
│   │       ├── historical_candles.py# /v3/historical-candle/...
│   │       ├── option_contract.py   # /v2/option/contract
│   │       ├── option_chain.py      # /v2/option/chain
│   │       ├── option_greeks.py     # /v3/market-quote/option-greek
│   │       ├── brokerage.py         # /v2/charges/brokerage
│   │       ├── orders.py            # v3 HFT place/modify/cancel + v2 reads + exit-all
│   │       ├── positions.py         # /v2/portfolio/short-term-positions
│   │       ├── rate_limiter.py      # per-second rate-limit helpers
│   │       ├── market_streamer.py   # MarketDataStreamerV3 wrapper (raw SDK)
│   │       └── portfolio_streamer.py# PortfolioDataStreamer wrapper (raw SDK)
│   ├── engines/
│   │   ├── init_engine/
│   │   ├── data_pipeline_engine/
│   │   ├── strategy_engine/
│   │   │   └── strategies/          # nifty50.py + banknifty.py + base.py
│   │   ├── order_execution_engine/
│   │   ├── background_engine/
│   │   ├── scheduler_engine/
│   │   ├── health_engine/
│   │   └── api_gateway/
│   ├── state/
│   │   ├── redis_client.py
│   │   ├── postgres_client.py
│   │   ├── keys.py                  # all Redis key constants under 6 namespaces
│   │   ├── streams.py
│   │   ├── lua/
│   │   └── schemas/                 # pydantic models
│   ├── deploy/
│   │   └── systemd/
│   ├── db/
│   │   └── migrations/
│   └── log_setup.py                 # central loguru config
├── frontend/                        # Next.js app (built later)
└── docs/
    ├── Strategy.md           # strategy + execution + flowcharts (single source for both)
    ├── Sequential_Flow.md    # daily lifecycle + boot/drain + readiness gates + recovery
    ├── HLD.md                # high-level architecture + topology + engines
    ├── TDD.md                # technical / per-engine implementation
    ├── Schema.md             # Redis + Postgres + pydantic data contracts
    ├── Modular_Design.md     # per-module function-signature index
    ├── API.md                # FastAPI REST + WebSocket spec
    ├── Frontend_Basics.md    # frontend contract (push-only, view keys, JWT, reconnect)
    └── LLM_Guidelines.md     # coding standards for LLM/human contributors
```
