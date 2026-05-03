# pcr-momentum

Premium-Diff Multi-Index Trading Bot — Python backend (FastAPI + asyncpg +
Redis), Next.js frontend, Upstox broker SDK.

> **For LLM contributors**: read [`docs/LLM_Onboarding.md`](./docs/LLM_Onboarding.md)
> first. It covers EC2 connection, the SSOT rule, GitHub workflow, and the
> PowerShell/SSH gotchas that catch every agent at least once.

## Documentation

All design docs live under [`docs/`](./docs):

### Foundation
- [`LLM_Onboarding.md`](./docs/LLM_Onboarding.md) — **read first**: EC2,
  SSOT, GitHub, PowerShell/SSH gotchas
- [`LLM_Guidelines.md`](./docs/LLM_Guidelines.md) — coding standards
- [`Project_Plan.md`](./docs/Project_Plan.md) — phased delivery plan
- [`Dev_Setup.md`](./docs/Dev_Setup.md) — operator + dev-machine runbook

### Architecture
- [`HLD.md`](./docs/HLD.md) — high-level design
- [`TDD.md`](./docs/TDD.md) — technical design / module contracts
- [`Modular_Design.md`](./docs/Modular_Design.md) — per-module
  responsibility & interface
- [`Strategy.md`](./docs/Strategy.md) — premium-diff momentum strategy spec
- [`Sequential_Flow.md`](./docs/Sequential_Flow.md) — system lifecycle &
  failsafes

### Contracts
- [`Schema.md`](./docs/Schema.md) — Redis + Postgres schema
- [`API.md`](./docs/API.md) — FastAPI gateway contract
- [`Frontend_Basics.md`](./docs/Frontend_Basics.md) — push-only WS + view
  contract

### Frontend (Phase 10)
- [`frontend/00_Frontend_Plan.md`](./docs/frontend/00_Frontend_Plan.md) —
  master plan, phase 10a/10b split, repo layout
- [`frontend/01_Design_System.md`](./docs/frontend/01_Design_System.md) —
  tokens, three themes (Slate Dark, Carbon Dark, Operator Light), Tailwind config
- [`frontend/02_App_Shell.md`](./docs/frontend/02_App_Shell.md) — layout,
  side nav, top bar, command palette
- [`frontend/03_Components.md`](./docs/frontend/03_Components.md) —
  component inventory + prop contracts
- [`frontend/04_Pages.md`](./docs/frontend/04_Pages.md) — every page
  detailed
- [`frontend/05_State_and_Data.md`](./docs/frontend/05_State_and_Data.md) —
  Zustand stores, WebSocket connection, REST client
- [`frontend/06_Charts_Analytics.md`](./docs/frontend/06_Charts_Analytics.md)
  — analytics page (Phase 10b) with chart spec
- [`frontend/07_Implementation_Order.md`](./docs/frontend/07_Implementation_Order.md)
  — step-by-step build sequence

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
| 10a | Frontend — core operator dashboard | **Not started** |
| 10b | Frontend Analytics + backend rollup endpoints | **Not started** |
| 11 | Hardening / systemd / TLS / Nginx | **Not started** |
| 12 | Paper-trade validation → live | **Not started** |

> **Single source of truth for code is the EC2 working tree** at
> `/home/ubuntu/premium_diff_bot/repo`. See
> [`docs/LLM_Onboarding.md`](./docs/LLM_Onboarding.md) for the connection
> recipe.
