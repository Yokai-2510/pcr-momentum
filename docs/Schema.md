# Schema Specification — Redis + PostgreSQL

This document is the complete schema reference. The Init Engine's `redis_template.py` is generated from Section 1. PostgreSQL migrations are derived from Section 2.

---

## 1. Redis Schema (6 Top-Level Namespaces)

All Redis keys live under one of six top-level namespaces, colon-separated:

```
1. system        → flags, health, lifecycle, scheduler, market calendar, control stream
2. user          → identity, credentials, broker auth, profile, capital
3. market_data   → instruments, ticks, option chains, subscription state, tick streams
4. strategy      → per-index state, configs, signals, ΔPCR, signal stream
5. orders        → positions, order lifecycle, broker portfolio, PnL, allocator, exec streams
6. ui            → view payloads, pub/sub channels, view rebuild queue, frontend streams
```

`{index}` is one of `nifty50`, `banknifty`. `{token}` is the Upstox instrument key (e.g. `NSE_FO|49520`).

---

### 1.1 `system:*` — System Flags, Health, Lifecycle, Scheduler

#### Flags (Init writes defaults; FastAPI / Health update at runtime)
| Key | Type | Default | Description |
|---|---|---|---|
| `system:flags:ready` | STRING | `"false"` | Set to `"true"` after Init completes |
| `system:flags:trading_active` | STRING | `"true"` | Master switch; `"false"` halts all new entries |
| `system:flags:trading_disabled_reason` | STRING | `"none"` | One of `none`, `awaiting_credentials`, `auth_invalid`, `holiday`, `manual_kill`, `circuit_tripped`. Whenever `!= "none"`, `trading_active` is also `"false"`. |
| `system:flags:mode` | STRING | `"paper"` | `"paper"` or `"live"` |
| `system:flags:daily_loss_circuit_triggered` | STRING | `"false"` | `"true"` when daily loss hits threshold |
| `system:flags:init_failed` | STRING | absent | Set with reason string if init fails |

#### Lifecycle (Init writes)
| Key | Type | Description |
|---|---|---|
| `system:lifecycle:start_ts` | STRING | Last Init run timestamp |
| `system:lifecycle:git_sha` | STRING | Build identifier |
| `system:lifecycle:last_shutdown_reason` | STRING | Reason of previous shutdown |

#### Health (Health Engine writes)
| Key | Type | Description |
|---|---|---|
| `system:health:summary` | STRING | `OK` / `DEGRADED` / `DOWN` |
| `system:health:engines` | HASH | `engine_name → JSON{alive, last_hb_ts, restart_count}` |
| `system:health:dependencies` | HASH | `redis, postgres, broker_market_ws, broker_portfolio_ws, broker_rest, auth → status` |
| `system:health:auth` | STRING | `valid` / `invalid` / `missing` / `unknown` (`missing` = no credentials in `user_credentials`; `invalid` = creds present but profile probe failed) |
| `system:health:heartbeats` | HASH | `engine_or_thread_name → epoch_ms_ts` (TTL on the HASH 5s; Health checks staleness) |
| `system:health:alerts` | STREAM | Rolling alert log |

Heartbeat field names:
```
init, data_pipeline, strategy:nifty50, strategy:banknifty, order_exec,
background:position_ws, background:pnl, background:delta_pcr:nifty50,
background:delta_pcr:banknifty, background:token_refresh,
background:capital_poll, background:kill_switch_poll,
scheduler, health, api_gateway
```

#### Scheduler (Scheduler Engine writes)
| Key | Type | Description |
|---|---|---|
| `system:scheduler:tasks` | HASH | `task_name → JSON{cron, last_run, next_run, status, event, target_engines}` |
| `system:scheduler:active` | SET | Currently-running task names |
| `system:scheduler:market_calendar:trading_days` | SET | ISO dates that are trading days |
| `system:scheduler:market_calendar:holidays` | SET | ISO dates that are holidays |
| `system:scheduler:market_calendar:session` | HASH | `{open, close, eod, pre_open_snapshot, delta_pcr_first}` |

#### Streams & Pub/Sub
| Key | Type | Description |
|---|---|---|
| `system:stream:control` | STREAM | Scheduler → all engines (events: pre_open_snapshot, session_open, eod_squareoff, session_close, graceful_shutdown, instrument_refresh, token_refresh_check) |
| `system:pub:system_event` | PUB/SUB | system_ready, shutdown, auth_refreshed, etc. |

---

### 1.2 `user:*` — Identity, Credentials, Auth, Profile, Capital

#### Identity (FastAPI writes on signup; Init hydrates from Postgres)
| Key | Type | Description |
|---|---|---|
| `user:account:username` | STRING | Single user's username |
| `user:account:jwt_secret` | STRING | JWT signing secret |
| `user:account:role` | STRING | `admin` (single-user system) |

#### Credentials (Init hydrates from Postgres encrypted; FastAPI rotation)
| Key | Type | Description |
|---|---|---|
| `user:credentials:upstox` | JSON | `{api_key, api_secret, redirect_uri, totp_key, mobile_no, pin, analytics_token}` |

#### Auth (Init bootstraps; Background refreshes)
| Key | Type | Description |
|---|---|---|
| `user:auth:access_token` | JSON | `{token, issued_at, expires_at, source}` |
| `user:auth:last_refresh_ts` | STRING | Epoch ms |

#### Profile (Background polls)
| Key | Type | Description |
|---|---|---|
| `user:profile:account` | JSON | `{user_id, exchanges, products, order_types, is_active, ...}` |

#### Capital (Background polls)
| Key | Type | Description |
|---|---|---|
| `user:capital:funds` | JSON | Full Upstox v3 funds-and-margin payload |
| `user:capital:kill_switch` | JSON | Per-segment kill switch status |
| `user:capital:static_ips` | JSON | `{primary, secondary, primary_updated_at, secondary_updated_at}` |

---

### 1.3 `market_data:*` — Instruments, Ticks, Option Chains, Subscriptions

#### Instruments (Init populates)
| Key | Type | Description |
|---|---|---|
| `market_data:instruments:master` | HASH | `token → JSON{symbol, expiry, strike, type, lot_size, exchange}` |
| `market_data:instruments:last_refresh_ts` | STRING | Epoch ms of last bulk refresh |

#### Per-Index Meta + Spot + Option Chain (Init populates meta; Data Pipeline writes spot + option_chain)
| Key | Type | Description |
|---|---|---|
| `market_data:indexes:{index}:meta` | JSON | `{strike_step, lot_size, exchange, spot_token, expiry, prev_close, atm_at_open, ce_strikes:[...], pe_strikes:[...]}` |
| `market_data:indexes:{index}:spot` | HASH | `{ltp, prev_close, change_pct, change_inr, ts}` (Data Pipeline updates per tick) |
| `market_data:indexes:{index}:option_chain` | JSON | **The core template.** Per-strike CE/PE template with WS-pushed live data. Single big JSON value, atomically updated by Data Pipeline. |

`option_chain` shape (one per index):
```json
{
  "24500": {
    "ce": {
      "token": "NSE_FO|49520",
      "ltp": 158.0, "bid": 157.5, "ask": 158.5,
      "bid_qty": 1500, "ask_qty": 1200,
      "vol": 234500, "oi": 67800, "ts": 1714290330123
    },
    "pe": {
      "token": "NSE_FO|49521",
      "ltp": 78.0, "bid": 77.5, "ask": 78.5,
      "bid_qty": 1800, "ask_qty": 1500,
      "vol": 198000, "oi": 89400, "ts": 1714290330123
    }
  },
  "24550": { "ce": {...}, "pe": {...} },
  ...
}
```

Data Pipeline's tick processor parses each WS frame, identifies `(index, strike, ce|pe)` from the token via `instruments:master`, and updates the leaf fields atomically.

#### Subscriptions (Data Pipeline owns)
| Key | Type | Description |
|---|---|---|
| `market_data:subscriptions:set` | SET | Currently subscribed broker tokens |
| `market_data:subscriptions:desired` | SET | Computed ATM ± 6 desired set per index |

#### Bars (Data Pipeline writes; optional, used for candle charts)
| Key | Type | Description |
|---|---|---|
| `market_data:bars:1s:{token}` | HASH | `{o, h, l, c, v, ts}` rolling 1-second OHLC |

#### Streams
| Key | Type | Description |
|---|---|---|
| `market_data:stream:tick:nifty50` | STREAM | DataPipe → Strategy.NIFTY (capped MAXLEN ~ 10000) |
| `market_data:stream:tick:banknifty` | STREAM | DataPipe → Strategy.BANKNIFTY |

#### WS Status (Data Pipeline + Background write)
| Key | Type | Description |
|---|---|---|
| `market_data:ws_status:market_ws` | HASH | `{connected, last_frame_ts, reconnect_count}` |
| `market_data:ws_status:portfolio_ws` | HASH | `{connected, last_frame_ts, reconnect_count}` |

---

### 1.4 `strategy:*` — Configs, Per-Index State, Signals, ΔPCR

#### Configs (Init hydrates from Postgres; FastAPI writes on edit)
| Key | Type | Description |
|---|---|---|
| `strategy:configs:execution` | JSON | `{buffer_inr, eod_buffer_inr, spread_skip_pct, drift_threshold_inr, chase_ceiling_inr, open_timeout_sec, partial_grace_sec, max_retries, worker_pool_size, liquidity_exit_suppress_after}` |
| `strategy:configs:session` | JSON | `{market_open, pre_open_snapshot, ws_subscribe_at, delta_pcr_first_compute, delta_pcr_interval_minutes, entry_freeze, eod_squareoff, market_close, graceful_shutdown, instrument_refresh}` |
| `strategy:configs:risk` | JSON | `{daily_loss_circuit_pct, max_concurrent_positions, trading_capital_inr}` |
| `strategy:configs:indexes:nifty50` | JSON | Full `IndexConfig` per Strategy.md §14 |
| `strategy:configs:indexes:banknifty` | JSON | Full `IndexConfig` per Strategy.md §14 |

#### Per-Index State (Strategy thread for that index writes)
| Key | Type | Description |
|---|---|---|
| `strategy:{index}:enabled` | STRING | `"true"` / `"false"` |
| `strategy:{index}:state` | STRING | `FLAT` / `IN_CE` / `IN_PE` / `COOLDOWN` / `HALTED` (Strategy.md §3) |
| `strategy:{index}:basket` | JSON | `{ce: [token1, token2, token3], pe: [token1, token2, token3]}` (locked at 09:15) |
| `strategy:{index}:pre_open` | JSON | Per-strike `{token: {pre_open_premium, best_bid, best_ask, oi}}` |
| `strategy:{index}:live:sum_ce` | STRING | Latest computed SUM_CE (rupees) |
| `strategy:{index}:live:sum_pe` | STRING | Latest computed SUM_PE (rupees) |
| `strategy:{index}:live:delta` | STRING | Latest `SUM_PE − SUM_CE` (signed; reversal trigger) |
| `strategy:{index}:live:diffs` | JSON | Per-strike Diff values for dashboard |
| `strategy:{index}:live:last_decision_ts` | STRING | Last tick processed (epoch ms) |
| `strategy:{index}:current_position_id` | STRING | If in position, the pos_id |
| `strategy:{index}:cooldown_until_ts` | STRING | Epoch ms; while now < this, state=COOLDOWN |
| `strategy:{index}:cooldown_reason` | STRING | `POST_SL` / `POST_REVERSAL` (telemetry) |
| `strategy:{index}:counters:entries_today` | STRING | All entries (initial + post-cooldown + flip-entries); cap at `max_entries_per_day` |
| `strategy:{index}:counters:reversals_today` | STRING | Cap at `max_reversals_per_day` |
| `strategy:{index}:counters:wins_today` | STRING | Counter |

#### Per-Index ΔPCR (Background ΔPCR thread for that index writes)
| Key | Type | Description |
|---|---|---|
| `strategy:{index}:delta_pcr:baseline` | JSON | Per-strike OI snapshot at 09:15 |
| `strategy:{index}:delta_pcr:last_oi` | JSON | Previous-interval OI per strike (for next diff) |
| `strategy:{index}:delta_pcr:interval` | HASH | `{interval_pcr, total_d_put, total_d_call, atm, ts}` |
| `strategy:{index}:delta_pcr:cumulative` | HASH | `{cumulative_pcr, cumulative_d_put, cumulative_d_call, ts}` |
| `strategy:{index}:delta_pcr:history` | LIST | List of past intervals (capped at 100) |
| `strategy:{index}:delta_pcr:last_compute_ts` | STRING | Epoch ms of last 3-min computation |
| `strategy:{index}:delta_pcr:mode` | STRING | `1` / `2` / `3` |

#### Signals (Strategy emits; Order Exec consumes)
| Key | Type | Description |
|---|---|---|
| `strategy:signals:{sig_id}` | JSON | Full signal payload (pydantic Signal model) |
| `strategy:signals:active` | SET | Currently in-flight sig_ids |
| `strategy:signals:counter` | STRING | Monotonic counter for sig_id generation |

#### Streams
| Key | Type | Description |
|---|---|---|
| `strategy:stream:signals` | STREAM | Strategy → Order Exec (consumer group `exec`) |
| `strategy:stream:rejected_signals` | STREAM | Audit only (no consumer) |

---

### 1.5 `orders:*` — Allocator, Positions, Orders, Broker State, PnL

#### Capital Allocator (Order Exec writes)
| Key | Type | Description |
|---|---|---|
| `orders:allocator:deployed` | HASH | `{nifty50, banknifty, total}` — premium deployed in rupees |
| `orders:allocator:open_count` | HASH | `{nifty50, banknifty, total}` — open position counts |
| `orders:allocator:open_symbols` | SET | Currently in-position indexes |

#### Positions (Order Exec writes)
| Key | Type | Description |
|---|---|---|
| `orders:positions:{pos_id}` | HASH | Full Position state per `state/schemas/position.py` |
| `orders:positions:open` | SET | All currently-open pos_ids globally |
| `orders:positions:open_by_index:nifty50` | SET | Open pos_ids for NIFTY (max 1) |
| `orders:positions:open_by_index:banknifty` | SET | Open pos_ids for BANKNIFTY (max 1) |
| `orders:positions:closed_today` | SET | All pos_ids closed today (cleared by Init) |

#### Orders (Order Exec writes)
| Key | Type | Description |
|---|---|---|
| `orders:orders:{order_id}` | HASH | Side, qty, limit_price, status, broker_resp, ts_submit, ts_ack |

#### Order Status (Order Exec writes for live frontend progress)
| Key | Type | Description |
|---|---|---|
| `orders:status:{pos_id}` | HASH | `{stage, substage, last_action, last_error, ts}` |

`stage` values: `GATE_PREENTRY`, `ENTRY_SUBMITTING`, `ENTRY_OPEN`, `ENTRY_FILLED`, `EXIT_EVAL`, `EXIT_SUBMITTING`, `EXIT_OPEN`, `EXIT_FILLED`, `REPORTING`, `CLEANUP`, `DONE`, `ABORTED`.

#### Broker Portfolio (Background's Position WS thread writes)
| Key | Type | Description |
|---|---|---|
| `orders:broker:pos:{order_id}` | HASH | Broker-side state: `{status, filled_qty, avg_price, raw_response, ts}` |
| `orders:broker:open_orders` | SET | Broker order_ids currently open at broker |

#### PnL (Background writes)
| Key | Type | Description |
|---|---|---|
| `orders:pnl:realized` | STRING | Cumulative realized PnL today |
| `orders:pnl:unrealized` | STRING | Currently open positions' MTM |
| `orders:pnl:per_index:nifty50` | HASH | `{realized, unrealized, trades_count, win_rate, avg_pnl_pct}` |
| `orders:pnl:per_index:banknifty` | HASH | Same |
| `orders:pnl:day` | HASH | `{realized, unrealized, trade_count, win_rate, day_pnl_pct_of_capital}` |

#### Streams
| Key | Type | Description |
|---|---|---|
| `orders:stream:order_events` | STREAM | Background → Order Exec, FastAPI |
| `orders:stream:manual_exit` | STREAM | FastAPI → Order Exec |

---

### 1.6 `ui:*` — View Payloads, Pub/Sub, Streams

#### View Keys (per HLD §7 — owner of underlying state rebuilds)
| Key | Type | Owner |
|---|---|---|
| `ui:views:dashboard` | JSON | Background |
| `ui:views:strategy:nifty50` | JSON | Strategy.NIFTY |
| `ui:views:strategy:banknifty` | JSON | Strategy.BANKNIFTY |
| `ui:views:position:nifty50` | JSON | Order Exec |
| `ui:views:position:banknifty` | JSON | Order Exec |
| `ui:views:positions_closed_today` | JSON | Order Exec |
| `ui:views:delta_pcr:nifty50` | JSON | Background |
| `ui:views:delta_pcr:banknifty` | JSON | Background |
| `ui:views:pnl` | JSON | Background |
| `ui:views:capital` | JSON | Background |
| `ui:views:health` | JSON | Health |
| `ui:views:configs` | JSON | Init + FastAPI |

#### Rebuild Coordination
| Key | Type | Description |
|---|---|---|
| `ui:dirty` | SET | View names awaiting debounced rebuild |

#### Pub/Sub
| Key | Type | Description |
|---|---|---|
| `ui:pub:view` | PUB/SUB | View rebuild notifications (payload = view key name) |

#### Streams
| Key | Type | Description |
|---|---|---|
| `ui:stream:health_alerts` | STREAM | Health → FastAPI |

---

### 1.7 Position HASH Schema (`orders:positions:{pos_id}`)

```
pos_id              str
sig_id              str
index               str             "nifty50" | "banknifty"
side                str             "CE" | "PE"
strike              int
instrument_token    str
qty                 int
entry_order_id      str
exit_order_id       str | null
entry_price         float           avg fill price
entry_ts            str             ISO timestamp
exit_price          float | null
exit_ts             str | null
mode                str             "paper" | "live"
intent              str             "FRESH_ENTRY" | "REVERSAL_FLIP"
sl_level            float           absolute premium price
target_level        float
tsl_armed           bool
tsl_arm_pct         float
tsl_trail_pct       float
tsl_level           float | null    null until armed
peak_premium        float           highest premium seen during hold
current_premium     float           latest tick
pnl                 float           current absolute pnl in rupees
pnl_pct             float           current pnl as % of entry premium
holding_seconds     int
exit_profile        JSON            {sl_pct, target_pct, tsl_arm_pct, tsl_trail_pct, max_hold_sec}
sum_ce_at_entry     float
sum_pe_at_entry     float
delta_pcr_at_entry  float
strategy_version    str
```

---

### 1.8 Single-Writer Ownership Map

| Top-level prefix | Owner Engine(s) |
|---|---|
| `system:flags` | Init (default) + FastAPI (manual override) + Health (auto-trip on circuit) |
| `system:lifecycle` | Init |
| `system:health` | Health |
| `system:scheduler` | Scheduler (tasks); Init (market_calendar from Postgres) |
| `system:stream:control` | Scheduler |
| `system:pub:system_event` | Init / Scheduler / FastAPI |
| `user:account` | FastAPI (signup, password change) |
| `user:credentials` | Init (boot) + FastAPI (rotation) |
| `user:auth` | Init (boot) + Background (refresh) + FastAPI (webhook) |
| `user:profile` | Background |
| `user:capital` | Background |
| `market_data:instruments` | Init |
| `market_data:indexes:{index}:meta` | Init (boot) + Scheduler (instrument_refresh event) |
| `market_data:indexes:{index}:spot`, `option_chain` | Data Pipeline (WS direct writes) |
| `market_data:subscriptions` | Data Pipeline |
| `market_data:bars` | Data Pipeline |
| `market_data:stream:tick:*` | Data Pipeline |
| `market_data:ws_status` | Data Pipeline (market_ws) + Background (portfolio_ws) |
| `strategy:configs` | Init (boot) + FastAPI (edit) |
| `strategy:{index}:state, basket, pre_open, live, current_position_id, counters, bootstrap_done` | Strategy Engine (the thread for that index) |
| `strategy:{index}:enabled` | Init (default `true`) + FastAPI (halt/resume) |
| `strategy:{index}:delta_pcr:*` | Background (ΔPCR thread for that index) |
| `strategy:signals:*` | Strategy Engine |
| `strategy:stream:signals, rejected_signals` | Strategy Engine |
| `orders:allocator:*` | Order Exec |
| `orders:positions:*` | Order Exec |
| `orders:orders:*` | Order Exec |
| `orders:status:*` | Order Exec |
| `orders:broker:*` | Background (position WS thread) |
| `orders:pnl:*` | Background (pnl thread) |
| `orders:stream:order_events` | Background |
| `orders:stream:manual_exit` | FastAPI |
| `ui:views:*` | per HLD §7 view ownership |
| `ui:dirty` | any (write only); view builders read+SREM |
| `ui:pub:view` | view builders |
| `ui:stream:health_alerts` | Health |

---

### 1.9 Daily Reset Behavior

When Init Engine runs at 04:01 IST, scope FLUSHDB to runtime namespaces only:

**Cleared on daily restart:**
- All keys under `market_data:indexes:*:spot`, `market_data:indexes:*:option_chain`, `market_data:bars:*`, `market_data:subscriptions:*`, `market_data:ws_status:*`
- All keys under `strategy:{index}:*` except `strategy:configs:*` and `strategy:{index}:enabled`
- All keys under `strategy:signals:*`, `strategy:stream:*`
- All keys under `orders:*` (positions, orders, status, broker, pnl, allocator, streams)
- All keys under `ui:views:*`, `ui:dirty`, `ui:stream:*`
- `system:flags:daily_loss_circuit_triggered`, `system:flags:init_failed`, `system:health:heartbeats`, `system:health:alerts`
- `market_data:stream:tick:*`

**Preserved across daily restart:**
- `system:lifecycle:git_sha`
- `system:scheduler:tasks`, `system:scheduler:market_calendar:*`
- `user:*` (re-hydrated from Postgres if changed)
- `strategy:configs:*` (re-hydrated from Postgres if changed)
- `market_data:instruments:master` (re-fetched from broker file)

---

### 1.10 Key Naming Conventions

- All Redis keys lowercase, colon-separated
- Index identifier always `nifty50` / `banknifty` (lowercase, no underscores between words, no spaces)
- Token format follows broker convention (`NSE_FO|49520`)
- Heartbeat field names use sub-thread suffix where multi-threaded (e.g. `background:delta_pcr:nifty50`)
- View keys always under `ui:views:`
- Streams always under `<top-level>:stream:`
- Pub/Sub channels always under `<top-level>:pub:`
- Postgres tables snake_case plural (`trades_closed_positions`)
- Postgres columns snake_case (`entry_ts`)
- Python identifiers snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE for constants

---

## 2. PostgreSQL Schema (5 Table Groups)

PostgreSQL 16, no extensions required. Tables grouped by purpose via name prefix.

### 2.1 `user_*` — Identity & Audit

```sql
CREATE TABLE user_accounts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username        TEXT UNIQUE NOT NULL,
  password_hash   TEXT NOT NULL,                    -- bcrypt
  jwt_secret      TEXT NOT NULL,
  role            TEXT DEFAULT 'admin',
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_credentials (
  broker          TEXT PRIMARY KEY,                 -- 'upstox'
  encrypted_value BYTEA NOT NULL,                   -- AES-GCM encrypted JSONB
  updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_audit_log (
  id              BIGSERIAL PRIMARY KEY,
  ts              TIMESTAMPTZ DEFAULT now(),
  user_id         UUID REFERENCES user_accounts(id),
  action          TEXT NOT NULL,
  ip              TEXT,
  user_agent      TEXT,
  payload         JSONB
);

CREATE INDEX idx_user_audit_log_ts ON user_audit_log(ts DESC);
CREATE INDEX idx_user_audit_log_user_ts ON user_audit_log(user_id, ts DESC);
```

### 2.2 `config_*` — Source of Truth for All Config

Postgres holds the durable copy; Init mirrors into Redis on every boot.

```sql
CREATE TABLE config_settings (
  key             TEXT PRIMARY KEY,                 -- 'execution', 'session', 'risk', 'index:nifty50', 'index:banknifty'
  value           JSONB NOT NULL,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  updated_by      UUID REFERENCES user_accounts(id)
);

CREATE TABLE config_task_definitions (
  id              TEXT PRIMARY KEY,
  engine          TEXT NOT NULL,
  name            TEXT NOT NULL,
  cron            TEXT,
  start_time      TIME,
  end_time        TIME,
  duration_s      INT,
  event_name      TEXT NOT NULL,
  target_engines  TEXT[] NOT NULL,
  enabled         BOOLEAN DEFAULT TRUE
);
```

### 2.3 `market_*` — Reference Data

```sql
CREATE TABLE market_calendar (
  date            DATE PRIMARY KEY,
  is_trading      BOOLEAN NOT NULL,
  session         JSONB,                            -- {open, close, eod}
  notes           TEXT
);

CREATE TABLE market_instruments_cache (
  ts              TIMESTAMPTZ NOT NULL,
  index           TEXT NOT NULL,
  expiry          DATE NOT NULL,
  instrument_key  TEXT NOT NULL,
  strike          INT,
  type            TEXT,                             -- 'CE' | 'PE'
  lot_size        INT,
  tick_size       NUMERIC,
  PRIMARY KEY (ts, instrument_key)
);

CREATE INDEX idx_market_instruments_index_expiry ON market_instruments_cache(index, expiry);
```

### 2.4 `trades_*` — Trading Records

```sql
CREATE TABLE trades_closed_positions (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  sig_id                   TEXT NOT NULL,
  index                    TEXT NOT NULL,                     -- 'nifty50' | 'banknifty'
  mode                     TEXT NOT NULL,                     -- 'paper' | 'live'
  side                     TEXT NOT NULL,                     -- 'CE' | 'PE'
  strike                   INT NOT NULL,
  instrument_token         TEXT NOT NULL,
  qty                      INT NOT NULL,
  entry_ts                 TIMESTAMPTZ NOT NULL,
  exit_ts                  TIMESTAMPTZ NOT NULL,
  holding_seconds          INT NOT NULL,
  entry_price              NUMERIC NOT NULL,
  exit_price               NUMERIC NOT NULL,
  pnl                      NUMERIC NOT NULL,
  pnl_pct                  NUMERIC NOT NULL,
  exit_reason              TEXT NOT NULL,                     -- HARD_SL / HARD_TARGET / TRAILING_SL / REVERSAL_FLIP / TIME_EXIT / EOD / LIQUIDITY / DAILY_LOSS_CIRCUIT / MANUAL
  intent                   TEXT NOT NULL,                     -- FRESH_ENTRY | REVERSAL_FLIP
  signal_snapshot          JSONB NOT NULL,
  pre_open_snapshot        JSONB NOT NULL,
  market_snapshot_entry    JSONB NOT NULL,
  market_snapshot_exit     JSONB NOT NULL,
  exit_eval_history        JSONB,
  trailing_history         JSONB,
  order_events             JSONB NOT NULL,
  latencies                JSONB NOT NULL,                    -- {signal_to_submit_ms, submit_to_ack_ms, ack_to_fill_ms, decision_to_exit_submit_ms, exit_submit_to_fill_ms}
  pnl_breakdown            JSONB NOT NULL,                    -- {gross, charges, slippage, net}
  delta_pcr_at_entry       NUMERIC,
  delta_pcr_at_exit        NUMERIC,
  raw_broker_responses     JSONB,
  strategy_version         TEXT NOT NULL,
  created_at               TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_trades_closed_entry_ts ON trades_closed_positions(entry_ts DESC);
CREATE INDEX idx_trades_closed_index_entry_ts ON trades_closed_positions(index, entry_ts DESC);
CREATE INDEX idx_trades_closed_mode_entry_ts ON trades_closed_positions(mode, entry_ts DESC);
CREATE INDEX idx_trades_closed_sig_id ON trades_closed_positions(sig_id);

CREATE TABLE trades_rejected_signals (
  id              BIGSERIAL PRIMARY KEY,
  ts              TIMESTAMPTZ DEFAULT now(),
  sig_id          TEXT NOT NULL,
  index           TEXT NOT NULL,
  reason          TEXT NOT NULL,
  signal_payload  JSONB
);

CREATE INDEX idx_trades_rejected_ts ON trades_rejected_signals(ts DESC);
CREATE INDEX idx_trades_rejected_index ON trades_rejected_signals(index, ts DESC);
```

### 2.5 `metrics_*` — Time-Series Records (regular tables, no Timescale)

Append-only tables for time-series. Indexed on `ts`. Manual pruning via cron job or SQL `DELETE` with date predicate.

```sql
CREATE TABLE metrics_order_events (
  id                   BIGSERIAL PRIMARY KEY,
  ts                   TIMESTAMPTZ NOT NULL,
  position_id          UUID,
  order_id             TEXT NOT NULL,
  index                TEXT,
  event_type           TEXT NOT NULL,                          -- SUBMIT | ACK | MODIFY | CANCEL | FILL | PARTIAL_FILL | REJECT
  broker_status        TEXT,
  payload              JSONB,
  internal_latency_ms  INT
);

CREATE INDEX idx_metrics_order_events_ts ON metrics_order_events(ts DESC);
CREATE INDEX idx_metrics_order_events_position ON metrics_order_events(position_id, ts DESC);
CREATE INDEX idx_metrics_order_events_order ON metrics_order_events(order_id);

CREATE TABLE metrics_pnl_history (
  id                BIGSERIAL PRIMARY KEY,
  ts                TIMESTAMPTZ NOT NULL,
  mode              TEXT NOT NULL,
  index             TEXT,                                       -- nullable for all-rollup
  realized          NUMERIC,
  unrealized        NUMERIC,
  open_count        INT,
  day_trades        INT,
  win_rate          NUMERIC
);

CREATE INDEX idx_metrics_pnl_history_ts ON metrics_pnl_history(ts DESC);
CREATE INDEX idx_metrics_pnl_history_index_ts ON metrics_pnl_history(index, ts DESC);

CREATE TABLE metrics_delta_pcr_history (
  id                  BIGSERIAL PRIMARY KEY,
  ts                  TIMESTAMPTZ NOT NULL,
  index               TEXT NOT NULL,
  spot                NUMERIC,
  atm                 INT,
  total_d_put_oi      BIGINT,
  total_d_call_oi     BIGINT,
  cumulative_d_put_oi BIGINT,
  cumulative_d_call_oi BIGINT,
  interval_pcr        NUMERIC,
  cumulative_pcr      NUMERIC,
  per_strike_breakdown JSONB
);

CREATE INDEX idx_metrics_delta_pcr_index_ts ON metrics_delta_pcr_history(index, ts DESC);

CREATE TABLE metrics_health_history (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  summary       TEXT NOT NULL,
  engines       JSONB,
  dependencies  JSONB
);

CREATE INDEX idx_metrics_health_history_ts ON metrics_health_history(ts DESC);

CREATE TABLE metrics_system_events (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ DEFAULT now(),
  event       TEXT NOT NULL,                                    -- INIT_START | INIT_SUCCESS | INIT_FAIL | SHUTDOWN | DAILY_LOSS_TRIGGER | MANUAL_KILL | AUTH_REFRESHED | etc.
  payload     JSONB
);

CREATE INDEX idx_metrics_system_events_ts ON metrics_system_events(ts DESC);
CREATE INDEX idx_metrics_system_events_event ON metrics_system_events(event, ts DESC);
```

### 2.6 Pruning (manual or cron)

`trades_closed_positions` and `trades_rejected_signals`: keep indefinitely.

`metrics_*`: prune monthly. Example cron job:
```sql
DELETE FROM metrics_order_events WHERE ts < now() - interval '180 days';
DELETE FROM metrics_pnl_history WHERE ts < now() - interval '365 days';
DELETE FROM metrics_delta_pcr_history WHERE ts < now() - interval '180 days';
DELETE FROM metrics_health_history WHERE ts < now() - interval '90 days';
DELETE FROM metrics_system_events WHERE ts < now() - interval '365 days';
VACUUM ANALYZE;
```

---

## 3. Logging (No External Infra)

### 3.1 Library
`loguru` configured to emit structured JSON to stdout.

```python
# backend/log_setup.py
from loguru import logger
import sys

def configure(engine_name: str):
    logger.remove()
    logger.add(
        sys.stdout,
        format="{message}",
        serialize=True,
        backtrace=False,
        diagnose=False,
    )
    logger.configure(extra={"engine": engine_name})
```

### 3.2 Log Format
Every line is one JSON object with required fields: `ts, level, engine, module, msg`. Recommended: `sig_id, pos_id, order_id, index` when relevant.

```json
{"ts":"2026-04-28T11:42:30.123+05:30","level":"INFO","engine":"order_exec","module":"worker","sig_id":"nifty50_1714290330123","pos_id":"abc-...","msg":"Entry order filled at 158.50, 1 lot"}
```

### 3.3 Where Logs Go
- Engines emit to stdout
- systemd captures stdout per service into journald
- Query via:
  ```
  journalctl -u trading-strategy.service -f                    # tail live
  journalctl -u trading-order-exec.service --since "09:15"     # since time
  journalctl -u trading-order-exec.service | grep "$sig_id"   # by signal
  ```
- `/etc/systemd/journald.conf` set `SystemMaxUse=2G` to cap disk usage; journald rotates automatically

### 3.4 Latency Tracking (no Prometheus)
Per-trade latency captured in `trades_closed_positions.latencies` JSONB column. Aggregate analysis via SQL:

```sql
SELECT index, mode,
       AVG((latencies->>'signal_to_submit_ms')::int) AS avg_signal_to_submit,
       PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY (latencies->>'ack_to_fill_ms')::int) AS p95_ack_to_fill,
       COUNT(*) AS n
FROM trades_closed_positions
WHERE entry_ts >= now() - interval '7 days'
GROUP BY index, mode;
```

For per-engine timing visibility outside trade boundaries, emit timed log entries:
```python
logger.info("event_processed", event="overtake", duration_ms=12)
```
Then `journalctl ... | jq 'select(.duration_ms != null)'` for ad-hoc analysis. No external aggregator needed at this scale.

### 3.5 What Never Goes to Logs
- Broker tokens, API keys, passwords, TOTP secrets — masked at the source (first 6 + last 4 chars)
- Raw market tick data — too high volume; lives only in Redis hot state and embedded in closed-position market_snapshots when needed for forensic analysis

---

## 4. Configuration JSON Shapes

### 4.1 `strategy:configs:execution`
```json
{
  "buffer_inr": 2,
  "eod_buffer_inr": 5,
  "spread_skip_pct": 0.05,
  "drift_threshold_inr": 3,
  "chase_ceiling_inr": 15,
  "open_timeout_sec": 8,
  "partial_grace_sec": 3,
  "max_retries": 2,
  "worker_pool_size": 8,
  "liquidity_exit_suppress_after": "15:00"
}
```

### 4.2 `strategy:configs:session`
```json
{
  "market_open": "09:15",
  "pre_open_snapshot": "09:14:50",
  "ws_subscribe_at": "09:14:00",
  "delta_pcr_first_compute": "09:18",
  "delta_pcr_interval_minutes": 3,
  "entry_freeze": "15:10",
  "eod_squareoff": "15:15",
  "market_close": "15:30",
  "graceful_shutdown": "15:45",
  "instrument_refresh": "05:30"
}
```

### 4.3 `strategy:configs:risk`
```json
{
  "daily_loss_circuit_pct": 0.08,
  "max_concurrent_positions": 2,
  "trading_capital_inr": 200000
}
```

### 4.4 `strategy:configs:indexes:{index}`

Full `IndexConfig` per Strategy.md §14. New (cooldown / cap / dominance) fields summarized:

```json
{
  "reversal_threshold_inr":        20,
  "entry_dominance_threshold_inr": 20,
  "post_sl_cooldown_sec":          60,
  "post_reversal_cooldown_sec":    90,
  "max_entries_per_day":           8,
  "max_reversals_per_day":         4,
  "...":                           "see Strategy.md §14 for full surface"
}
```

---

## 5. Pydantic Schema Models (`backend/state/schemas/`)

| Module | Models |
|---|---|
| `signal.py` | `Signal`, `SignalIntent` (enum) |
| `order_event.py` | `OrderEvent`, `OrderEventType` (enum) |
| `position.py` | `Position`, `ExitProfile`, `ExitReason` (enum), `PositionStage` (enum) |
| `pnl.py` | `PerIndexPnL`, `DayPnL` |
| `report.py` | `ClosedPositionReport`, `MarketSnapshot`, `OrderEventEntry`, `Latencies`, `PnLBreakdown` |
| `health.py` | `HealthSummary`, `EngineStatus`, `DependencyStatus` |
| `delta_pcr.py` | `DeltaPCRInterval`, `DeltaPCRCumulative`, `DeltaPCRHistoryEntry` |
| `view.py` | `DashboardView`, `PositionView`, `StrategyView`, `DeltaPCRView`, `PnLView`, `HealthView`, `CapitalView`, `ConfigsView` |
| `config.py` | `ExecutionConfig`, `SessionConfig`, `RiskConfig`, `IndexConfig` |
| `instruments.py` | `OptionContract`, `IndexMeta`, `OptionChainEntry` |
