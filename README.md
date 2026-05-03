# pcr-momentum

Premium-Diff Multi-Index Trading Bot — Python backend (FastAPI + asyncpg +
Redis), Next.js frontend, Upstox broker SDK.

## Documentation

All design docs live under [docs/](./docs):

- HLD.md — high-level design
- TDD.md — technical design / module contracts
- Modular_Design.md — per-module responsibility & interface
- Strategy.md — premium-diff momentum strategy spec
- Sequential_Flow.md — system lifecycle & failsafes
- Schema.md — Redis + Postgres schema
- API.md — FastAPI gateway contract
- Frontend_Basics.md — push-only WS + view contract
- Dev_Setup.md — operator + dev-machine setup runbook
- LLM_Guidelines.md — coding standards for contributors
- Project_Plan.md — phased delivery plan

## Status

| Phase | Engine | Status |
|---|---|---|
| 0 | Infrastructure bootstrap (EC2, Redis, Postgres, .venv) | **Complete** |
| 1 | Project skeleton + shared primitives (state/, log_setup.py) | **Complete** |
| 2 | Database migrations (Alembic 
