# pcr-momentum

Multi-index options trading bot for the Indian market (NSE F&O). Trades NIFTY 50 and BANKNIFTY using a premium-diff momentum strategy with ΔPCR overlay, daily auto-cycled engine stack, and a push-only frontend contract.

## At a glance

- **Strategy**: per-index state machine (FLAT / IN_CE / IN_PE / COOLDOWN / HALTED) driven by SUM_CE − SUM_PE premium-difference signals, optionally vetoed by 3-minute ΔPCR readings.
- **Topology**: 8 Python engines (init, data-pipeline, strategy, order-exec, background, scheduler, health, api-gateway) on a single EC2 host. Redis 7 for hot state, Postgres 16 for durable state.
- **Lifecycle**: cyclic engines wake at 08:00 IST and drain at 15:45 IST weekdays via systemd timers. Postgres + Redis + Nginx + FastAPI are persistent 24×7 so the operator UI stays reachable overnight.
- **Frontend**: Next.js on Vercel; talks to the backend over JWT-auth'd REST + a single WebSocket. No business logic in the client.

## Live deployment

| | |
|---|---|
| API | `https://3-6-128-21.sslip.io/api/` |
| WebSocket | `wss://3-6-128-21.sslip.io/stream` |
| Health | `https://3-6-128-21.sslip.io/api/health` |
| OpenAPI | `https://3-6-128-21.sslip.io/api/docs` |

## Repo layout

```
backend/
  brokers/upstox/        Upstox SDK facade (auth, REST, WS streamers)
  engines/               one process per engine
  state/                 redis client, postgres pool, key namespace, schemas
  alembic/               Postgres migrations
  tests/                 unit + integration
docs/                    design + contracts (read these first)
scripts/
  systemd/               pcr-*.service / pcr-*.target / pcr-*.timer
  nginx/pcr.conf         reverse proxy vhost
  cron/pcr-pg-backup     nightly pg_dump
  logrotate/pcr          engine + backup log rotation
  pcr-shutdown.sh        graceful drain script (15:45 IST)
  setup/                 one-time EC2 bring-up scripts
frontend/                Next.js app (Vercel-hosted; not on EC2)
```

## Documentation

Start here:

- [`docs/Project_Plan.md`](./docs/Project_Plan.md) — phased delivery + status
- [`docs/Checkpoint.md`](./docs/Checkpoint.md) — current deployment state + cheat-sheet
- [`docs/Dev_Setup.md`](./docs/Dev_Setup.md) — operator runbook (EC2, TLS, systemd, nginx, backups)

Architecture:

- [`docs/HLD.md`](./docs/HLD.md) — topology, engines, streams, hot-path discipline
- [`docs/Sequential_Flow.md`](./docs/Sequential_Flow.md) — daily lifecycle, readiness gates, drain ordering, recovery
- [`docs/Strategy.md`](./docs/Strategy.md) — premium-diff momentum spec with worked example
- [`docs/Modular_Design.md`](./docs/Modular_Design.md) — module-level function signatures
- [`docs/TDD.md`](./docs/TDD.md) — per-engine implementation contracts

Contracts:

- [`docs/Schema.md`](./docs/Schema.md) — Redis + Postgres shapes
- [`docs/API.md`](./docs/API.md) — FastAPI REST + WebSocket spec
- [`docs/Frontend_Integration.md`](./docs/Frontend_Integration.md) — how the UI talks to the backend (auth, WS, errors, rate limits)
- [`docs/Frontend_Basics.md`](./docs/Frontend_Basics.md) — push-only view protocol

Frontend (Phase 10) plans live under [`docs/frontend/`](./docs/frontend).

## Tech stack

- Python 3.12 + uvloop + asyncio
- Redis 7 (Unix socket, no persistence, AOF off)
- PostgreSQL 16
- FastAPI + websockets + orjson + asyncpg
- Upstox v2 + v3 (REST, market-data WS, portfolio WS)
- systemd-managed processes; Nginx + Let's Encrypt for TLS
- Next.js 14 (frontend, Vercel-hosted)

## Operating it

Bring-up and maintenance procedures live in [`docs/Dev_Setup.md`](./docs/Dev_Setup.md) and the `scripts/systemd/install.sh` installer:

```bash
sudo bash scripts/systemd/install.sh
sudo certbot --nginx -d <hostname>     # one-time TLS
systemctl status pcr-stack.target
journalctl -u "pcr-*" -f
```

Per-day lifecycle, failure modes, and recovery are documented in [`docs/Sequential_Flow.md`](./docs/Sequential_Flow.md).
