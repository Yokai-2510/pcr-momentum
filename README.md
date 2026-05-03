# pcr-momentum

Premium-Diff Multi-Index Trading Bot — Python backend (FastAPI + asyncpg +
Redis), Next.js frontend, Upstox broker SDK.

## Documentation

All design docs live under [`docs/`](./docs):

- `HLD.md` — high-level design
- `TDD.md` — technical design / module contracts
- `Modular_Design.md` — per-module responsibility & interface
- `Strategy.md` — premium-diff momentum strategy spec
- `Sequential_Flow.md` — system lifecycle & failsafes
- `Schema.md` — Redis + Postgres schema
- `API.md` — FastAPI gateway contract
- `Frontend_Basics.md` — push-only WS + view contract
- `Dev_Setup.md` — operator + dev-machine setup runbook
- `LLM_Guidelines.md` — coding standards for contributors
- `Project_Plan.md` — phased delivery plan

## Status

| Phase | Engine | Status |
|---|---|---|
| 0 | Infrastructure bootstrap (EC2, Redis, Postgres, .venv) | **Complete** |
| 1 | Project skeleton + shared primitives (`state/`, `log_setup.py`) | **Complete** |
| 2 | Database migrations (Alembic `0001_initial`) | **Complete** |
| 3 | Broker SDK (UpstoxAPI facade, 21 modules + replay harness) | **Complete** |
| 4 | Init engine (12-step bootstrapper) | **Complete** |
| 5 | Data pipeline (WebSocket ingest, tick processor, aggregator) | **Complete** |
| 6 | Strategy engine (premium-diff logic, 5-state machine) | **Complete** |
| 7 | Order execution (allocator, dispatcher, entry/exit gates) | **Complete** |
| 8 | Background, scheduler, health | **Complete** |
| 9 | FastAPI gateway (`api_gateway/` — REST + `/stream` WebSocket, JWT, rate limit) | **Complete** |
| 10 | Frontend (Next.js) | **Not started** |
| 11 | Hardening / systemd / TLS / Nginx | **Not started** |
| 12 | Paper-trade validation → live | **Not started** |

> **Single source of truth for code is the EC2 working tree** at `/home/ubuntu/premium_diff_bot/repo`.
