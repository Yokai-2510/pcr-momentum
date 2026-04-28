# Project Plan — Premium-Diff Multi-Index Trading Bot

> **Source of truth for delivery.** Each phase has explicit scope, deliverables,
> exit criteria, and dependencies. Do not start a phase until the previous one
> meets its exit criteria.
>
> Repo: `git@github.com:Yokai-2510/pcr-momentum.git`
> Working tree on EC2: `/home/ubuntu/premium_diff_bot/repo`

---

## Cross-cutting rules

These apply to every phase and are non-negotiable.

- **Single source of truth for code is the EC2 working tree.** All edits land
  there; no parallel forks on Windows. (Local files exist only as scratch for
  scp.)
- **One PR per phase, merged into `main` only when exit criteria pass.**
  Feature branches: `phase-{N}-{slug}` (e.g. `phase-3-broker-sdk`).
- **Tests come with code, not after.** A phase ends only when its tests are
  green in CI.
- **Doc sync is part of every PR.** If `Schema.md`, `API.md`, `Strategy.md`,
  `Modular_Design.md`, or `Sequential_Flow.md` is affected, the PR updates them
  in the same commit set.
- **Secrets stay out of git.** `.env`, `credentials.json`, `*.pem`,
  `.db_secret` are git-ignored and live only on the EC2.
- **Replay first, paper-trade second, live last.** Every strategy/order path
  must pass replay tests before any paper-trade run, and pass 5 consecutive
  paper-trade days before any live capital.

---

## Phase 0 — Infrastructure Bootstrap

**Status:** in progress (~80% done).

| Item | Status |
|---|---|
| EC2 (Ubuntu 24.04, 2 vCPU, 1.9 GB RAM, 15 GB disk) provisioned | done |
| 2 GB swap added & persisted | done |
| System packages: redis, postgres-16, nginx, certbot, ufw, fail2ban, chrony, unattended-upgrades | done |
| Python 3.12 venv at `/home/ubuntu/premium_diff_bot/.venv` with all `requirements.txt` deps | done |
| Playwright Chromium browser installed | done |
| Redis: Unix socket only, no persistence, 1 GB LRU cap | done |
| Postgres: `trader` role + `premium_diff_bot` DB + scram-sha-256 | done |
| `.env` populated with secrets + Upstox creds | done |
| Smoke check script (`scripts/setup/smoke_check.py`) passes ALL_OK | done |
| GitHub deploy key installed; `Yokai-2510/pcr-momentum` cloned to `/home/ubuntu/premium_diff_bot/repo` | done |
| `.gitignore`, `.env.example`, README, docs pushed to `main` | done |
| **ufw enabled with `22/80/443` allow + `deny incoming` default** | pending |
| **fail2ban sshd jail tuned (basic ssh-iptables, maxretry=5)** | pending |
| **AWS Security Group: open 80 & 443 (ingress), tighten 22 to operator IP** | pending — user action |
| **Allocate Elastic IP & associate with instance** | pending — user action |

### Phase 0 exit criteria
- `bash scripts/setup/audit.sh` passes with no warnings (services active, ufw
  active, ports as expected, .env keys present).
- `python scripts/setup/smoke_check.py` returns `RESULT: ALL_OK`.
- A trivial commit can be pushed from EC2 to GitHub on `main`.

---

## Phase 1 — Project Skeleton + Shared Primitives

**Goal:** lay the canonical layout from `Modular_Design.md` and ship the
foundation modules every engine will import.

### Scope

- `backend/` package layout matching `HLD.md` §11 / `Modular_Design.md`:
  - `engines/init/`, `engines/data_pipeline/`, `engines/strategy/`,
    `engines/order_exec/`, `engines/background/`, `engines/scheduler/`,
    `engines/health/`, `engines/api_gateway/`
  - `brokers/upstox/`
  - `shared/` (redis_client, postgres_client, keys, streams, schemas, lua,
    logger, config_loader)
  - `tests/`
- Shared primitives — fully implemented + unit-tested:
  - `shared/keys.py` — typed key namespace builder (matches `Schema.md`)
  - `shared/redis_client.py` — async Redis wrapper, Lua loader, pipelines
  - `shared/postgres_client.py` — asyncpg pool factory, transaction helpers
  - `shared/streams.py` — XADD/XREADGROUP wrappers, stream constants
  - `shared/schemas/` — Pydantic models for every payload in `Schema.md` §3
  - `shared/logger.py` — loguru config (JSON in prod, pretty in dev)
  - `shared/config_loader.py` — `.env` + Postgres `config_*` table loader
- Lua scripts under `shared/lua/` with a tiny test harness.
- `pytest` + `pytest-asyncio` set up; `fakeredis` for unit tests.
- CI: GitHub Actions workflow `ci.yml` running `ruff`, `mypy`, `pytest` on
  push.

### Deliverables
- `feature/phase-1-skeleton` branch merged into `main`.
- `pytest` green; `ruff` clean; `mypy --strict shared/` clean.
- `docs/Modular_Design.md` updated to reflect any concrete signature changes.

### Exit criteria
- A demo script can `from shared.redis_client import RedisClient` and
  `from shared.postgres_client import PgPool`, connect, write, read.
- All Pydantic schemas serialize/deserialize round-trip in tests.

---

## Phase 2 — Database Migrations (Alembic)

**Goal:** reify `Schema.md` Postgres tables in a versioned migration history.

### Scope
- `backend/alembic/` initialised against `DATABASE_URL`.
- Migration `0001_initial.py` creates all `user_*`, `config_*`, `market_*`,
  `trades_*`, `metrics_*`, `logs_*` tables exactly as specified in
  `Schema.md`.
- Migration `0002_seed.py` inserts:
  - one `users` row (admin, password = `$SEED_ADMIN_PASSWORD` from .env)
  - default `config_strategy`, `config_risk`, `config_indices` rows
  - default `config_instruments_refresh_schedule` row
- Idempotent re-run via `alembic upgrade head`.
- Tests: spin up a temp DB, run migrations, assert schema with
  `pg_dump --schema-only` snapshot.

### Exit criteria
- `alembic upgrade head` against the production Postgres succeeds.
- `psql -d premium_diff_bot -c '\dt'` shows the full table list from
  `Schema.md`.
- Admin can authenticate against the seeded user (after Phase 9 wires the
  endpoint).

---

## Phase 3 — Broker SDK (UpstoxAPI Facade)

**Goal:** deliver the 21-module broker layer with the `UpstoxAPI` facade as
the only public entry point.

### Scope (matches `Modular_Design.md` §3 + `client.py`)
- Auth & session: `auth.py`, `session.py`, `login_automation.py` (Playwright
  + TOTP + PIN), `token_refresh.py`.
- REST modules: `instruments.py`, `instrument_search.py`, `option_chain.py`,
  `option_contract.py`, `market_quote.py`, `market_depth.py`, `historical.py`,
  `intraday.py`, `orders.py`, `portfolio.py`, `positions.py`, `funds.py`,
  `charges.py`, `brokerage.py`, `trade_history.py`.
- Streamers: `market_data_stream.py`, `portfolio_stream.py`.
- Helpers: `errors.py`, `envelopes.py`, `rate_limit.py`.
- Facade: `client.py` exposing `UpstoxAPI` with stateless classmethods.
- Replay harness: `tests/broker/replay/` with captured HTTP fixtures (use
  `respx` against `httpx`) and a fake WebSocket stream player.
- Login automation E2E test: with the **dummy** Upstox account creds, run
  Playwright login, fetch token, call `funds()`, assert success.
  Headless, runs nightly in CI (manual trigger only — not on every push, to
  protect rate limits and prevent flapping if Upstox changes pages).

### Exit criteria
- `UpstoxAPI.funds(params={})` returns a `200 OK` envelope when run on EC2
  using the dummy creds in `.env`.
- `UpstoxAPI.option_chain(params={"index": "NIFTY", "expiry": "..."})` returns
  a non-empty chain.
- WebSocket streamer can connect, subscribe to NIFTY ATM strike, receive ≥10
  ticks within 30 s.
- Replay tests cover every classmethod with at least one happy + one error
  fixture.

---

## Phase 4 — Init Engine (Bootstrapper)

**Goal:** implement the 12-step init sequence from `Sequential_Flow.md` §6.

### Scope
- `engines/init/main.py` — the entrypoint, runs steps 1-12 in order.
- Steps:
  1. Load `.env`, validate required keys.
  2. Connect Redis + Postgres; abort on infra fail (exit 1).
  3. Bootstrap users + configs from DB; create defaults if missing.
  4. Holiday/weekend gate; if non-trading day → idle, exit 0.
  5. Instrument refresh: fetch + cache to Redis + Postgres.
  6. **Credential bootstrap:** if Upstox creds missing/invalid → set
     `system:flags:trading_disabled_reason=awaiting_credentials` and idle
     (exit 0). Stack still comes up so user can submit creds via UI.
  7. Capital snapshot: query `funds()`, write to `system:health:capital`.
  8. Build per-index strike basket (range from config).
  9. Cache warm-up: pre-fetch ATM ±N option_chain rows.
  10. Verify dependencies (Redis/Postgres/broker reachable).
  11. Arm Scheduler with daily triggers.
  12. Set `system:flags:init_complete=1`, publish to `system:status` stream.
- All steps are idempotent and individually testable.

### Exit criteria
- `python -m engines.init` runs to completion on a fresh DB; logs show all 12
  steps passing.
- Holiday gate correctly skips on a Saturday (test by injecting fake `today`).
- Credential-missing path leaves `init_complete=1` AND
  `trading_disabled_reason=awaiting_credentials`.

---

## Phase 5 — Data Pipeline Engine

**Goal:** the always-on tick ingestion + aggregation engine.

### Scope
- `engines/data_pipeline/main.py` connects to `UpstoxAPI.market_data_stream`,
  subscribes to the full strike basket per index.
- `aggregator.py` writes raw ticks to Redis hashes
  (`market_data:{index}:{strike}:{type}`) atomically.
- `resampler.py` produces 1s, 5s, 1min OHLCV candles in Redis Streams.
- `backfill.py` on reconnect fetches missing window via REST.
- Backpressure: drop oldest if Redis writes >100 ms; emit alert.
- Dead-tick detection: if no tick for any subscribed instrument >10 s, mark
  `market_data:health=stale`.

### Exit criteria
- Replay test: feed 60 minutes of recorded ticks, assert candles in Redis
  match expected snapshot.
- Live test on EC2 with dummy creds: subscribe to NIFTY ATM, run for 5
  minutes during market hours, observe Redis keys + candle streams populated.
- Reconnect test: kill the WS, observe automatic reconnect + backfill.

---

## Phase 6 — Strategy Engine

**Goal:** the 5-state machine and premium-diff momentum logic.

### Scope (per `Strategy.md`)
- One thread per index — single-writer to `strategy:{index}:state` and
  `positions:{index}:open`.
- `decision.py` — pure functions matching `Modular_Design.md` signatures.
- States: `IDLE`, `ARMED`, `IN_POSITION`, `COOLDOWN`, `HALTED`.
- Entry triggers: premium-diff threshold + ΔPCR confirmation overlay.
- Exit rules: stop-loss, take-profit, trailing, EOD square-off, manual.
- Cooldowns: per-trigger and post-flip.
- Daily caps: max trades/day, max loss/day → transition to `HALTED`.
- Both-negative recovery: defined in `Strategy.md`.
- Reversal flips: when allowed, exit + cooldown + flip.

### Exit criteria
- Replay test: run `Strategy.md` worked example tick-by-tick; output matches
  expected entry/exit timing within ±1 tick.
- Cooldown + cap tests with synthetic event streams.
- No stale state: a kill+restart of the engine resumes from Redis without
  duplicating positions.

---

## Phase 7 — Order Execution Engine

**Goal:** the only writer to broker order state; all order intents flow
through here.

### Scope
- `engines/order_exec/main.py` consumes order intents from
  `orders:intent:stream`, places via `UpstoxAPI.orders.place()`.
- Bracket order semantics: place + attach SL + TP in correct sequence.
- Order state machine: `PENDING` → `ACK` → `FILLED` / `REJECTED` /
  `CANCELLED`; persisted to `orders:state:{order_id}` and Postgres
  `trades_orders`.
- Risk gates **before** any place call:
  - capital available
  - max-loss-today not breached
  - kill-switch off
  - daily-trade-count under cap
- Reconciliation: on startup, sync open orders from broker to local state.
- Manual exit endpoint plumbed through this engine.

### Exit criteria
- Replay test: place + fill + SL-trigger sequence completes; Postgres rows
  consistent.
- Kill-switch test: flip `system:flags:kill_switch=1`, observe new intents
  rejected with reason logged.
- Reject test: send a malformed intent, observe REJECTED state with reason.

---

## Phase 8 — Background, Scheduler, Health

**Goal:** the supervisory engines.

### Scope
- **Scheduler** (`engines/scheduler/`) — APScheduler with cron triggers:
  pre-open snapshot (08:55 IST), market open (09:15), EOD square-off (15:20),
  daily reset (15:35), nightly maintenance (02:00).
- **Background** (`engines/background/`) — long-running maintenance:
  instrument refresh (daily), log rotation, Postgres `VACUUM ANALYZE`.
- **Health** (`engines/health/`) — probes Redis, Postgres, broker REST,
  broker WS, system load, swap usage; writes `system:health:*` keys, emits
  alerts on failure.

### Exit criteria
- Daily lifecycle dry-run: fast-forward simulated clock, observe each cron
  fires and writes the expected effect.
- Health endpoint (Phase 9 dependency): returns green on a healthy stack,
  red with reason on injected failures.

---

## Phase 9 — FastAPI Gateway

**Goal:** the only thing the frontend talks to.

### Scope (per `API.md`)
- Auth: `/auth/login`, `/auth/refresh`, `/auth/upstox-webhook`.
- Configs: `GET /configs`, `PUT /configs/{section}`.
- Strategy control: `/commands/halt_index`, `/commands/resume_index`,
  `/commands/global_kill`, `/commands/global_resume`.
- Positions: `/positions/open`, `/positions/closed_today`,
  `/positions/history`, `/reports/{position_id}`,
  `/commands/manual_exit/{id}`.
- PnL: `/pnl/live`, `/pnl/history`.
- ΔPCR: `/delta_pcr/{index}/live`, `/delta_pcr/{index}/history`,
  `/delta_pcr/{index}/mode`.
- Health: `/health`, `/health/dependencies/test`.
- Operational: `/commands/instrument_refresh`,
  `/commands/upstox_token_request`, `/capital/funds`, `/capital/kill_switch`.
- **Credentials:** `GET /credentials/upstox`, `POST /credentials/upstox`,
  `DELETE /credentials/upstox` (AES-256-GCM via `CREDS_ENCRYPTION_KEY`).
- WebSocket `/ws` — push-only protocol per `Frontend_Basics.md` §3-4.
- JWT middleware, CORS, rate limits per `API.md` §6.

### Exit criteria
- `pytest tests/api/` covers every endpoint with happy + auth-fail + bad-input.
- WS smoke test: connect, receive snapshot, then receive ≥1 patch event.
- OpenAPI spec at `/docs` renders all endpoints with correct schemas.

---

## Phase 10 — Frontend (Next.js)

**Goal:** the operator UI.

### Scope (per `Frontend_Basics.md`)
- App shell: Next.js 14 (app router) + TailwindCSS + shadcn/ui + Lucide.
- WS client: connect, JWT auth, exponential backoff, full-replacement
  view rendering (no merging).
- Pages:
  - `/login` — username/password.
  - `/onboarding/credentials` — first-boot Upstox credential wizard
    (`Frontend_Basics.md` §10).
  - `/dashboard` — live positions, PnL, ΔPCR per index, system flags.
  - `/configs` — strategy, risk, indices.
  - `/positions` — closed-today + history.
  - `/reports/[id]` — single-position report.
  - `/operations` — manual exit, instrument refresh, token request, kill.
- Degraded-mode banner when `trading_disabled_reason != none`.
- Build & served by Nginx (Phase 11).

### Exit criteria
- Lighthouse score ≥90 on dashboard.
- Manual UAT script: log in → credential wizard → see live ticks → halt
  index → resume → manual exit a paper trade.
- WS reconnect resilience: kill backend, frontend shows degraded banner;
  bring back, full state replaces within 2 s.

---

## Phase 11 — Hardening, systemd, Nginx, TLS, Backups

**Goal:** make it boot-on-its-own and survive a power cycle.

### Scope
- systemd units: `trading-init.service`, `trading-data-pipeline.service`,
  `trading-strategy@.service` (templated per index), `trading-order-exec.service`,
  `trading-background.service`, `trading-scheduler.service`,
  `trading-health.service`, `trading-api.service`, `trading-frontend.service`.
- `trading-stack.target` to bring them all up in dependency order.
- Nginx reverse proxy: `/` → frontend (3000), `/api` → backend (8000), `/ws`
  → backend WS upgrade.
- TLS via Let's Encrypt (`certbot --nginx`).
- Postgres nightly backup → `/var/backups/pg/` + cron rotation (7 days).
- Log rotation via `logrotate.d/trading`.
- Disaster-recovery runbook in `docs/Runbook.md`.
- Reboot test: stop instance, start instance, verify the entire stack comes
  back idle (no creds dependency) within 60 s.

### Exit criteria
- `systemctl reboot` survives without manual intervention; `audit.sh` passes
  post-reboot.
- TLS cert valid; `https://<domain>` serves frontend; WS over WSS works.
- Postgres backup script runs and produces a restorable dump.

---

## Phase 12 — Paper-Trade & Go-Live

**Goal:** prove the system on real ticks before risking capital.

### Scope
- 5 consecutive paper-trade days using dummy Upstox creds during live market
  hours.
- Daily review: PnL log, exception log, latency histogram, Redis memory
  trend.
- Tune cooldowns / thresholds based on observation.
- Switch to real account credentials.
- Day 1 live with capped capital (e.g. ₹10K notional per trade).
- 1-week observation period before scaling capital.

### Exit criteria
- Zero unhandled exceptions across 5 paper-trade days.
- p95 tick→decision latency under 50 ms.
- p95 decision→order-ack latency under 250 ms.
- All trades reconciled between local DB and broker portfolio at EOD.
- Client UAT signed off.

---

## Recommended order of operations

```
[done]      Phase 0  — Infra
            └── enable ufw + tighten SG (small remainder)
[next]      Phase 1  — Skeleton + shared primitives
            Phase 2  — Alembic migrations
            Phase 3  — Broker SDK
            Phase 4  — Init engine
            Phase 5  — Data pipeline
            Phase 6  — Strategy
            Phase 7  — Order execution
            Phase 8  — Background / Scheduler / Health
            Phase 9  — FastAPI gateway
            Phase 10 — Frontend
            Phase 11 — Hardening / systemd / TLS
            Phase 12 — Paper-trade → live
```

### Notes
- Phases 1-2 are tightly coupled; ship them together.
- Phase 3 can run partially in parallel with Phase 2 (different files).
- Phase 9 depends on Phases 1-8 to be meaningful, but the auth + health
  endpoints can ship as soon as Phase 1 lands.
- Phase 10 can begin once Phase 9 has a stable `/health` and `/auth/login`.
- Phase 11 can be done incrementally — systemd unit per engine as that
  engine stabilises.

---

## What "shipping ready" means

The system is **client-shipping-ready** when every box is ticked:

- [ ] All 12 phases passed exit criteria.
- [ ] CI green on `main`.
- [ ] 5 consecutive paper-trade days clean.
- [ ] Disaster-recovery runbook tested (forced failover, cert renewal,
      Postgres restore-from-backup).
- [ ] Operator runbook covers: kill switch, manual exit, credential
      rotation, instance reboot.
- [ ] Client has admin password + URL + a 30-min walkthrough video.

