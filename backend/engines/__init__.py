"""Engine package — one subpackage per long-running process.

Per `docs/HLD.md` §11 + `docs/Modular_Design.md`, the eight engines are:

    init           — boot sequence (Phase 4)
    data_pipeline  — tick ingestion + aggregation (Phase 5)
    strategy       — premium-diff state machine (Phase 6)
    order_exec     — broker-side order lifecycle (Phase 7)
    background     — supervisory + non-hot-path workers (Phase 8)
    scheduler      — APScheduler cron triggers (Phase 8)
    health         — liveness + dependency probes (Phase 8)
    api_gateway    — FastAPI + WS surface (Phase 9)

Strict rules (Modular_Design.md §1):
- Engines never import each other.
- Engines never import individual broker modules; they go through
  `brokers.upstox.UpstoxAPI`.
- Every engine imports from `state/*` and `log_setup`.
"""
