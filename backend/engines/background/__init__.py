"""Background engine — supervisory long-running tasks.

Per docs/Project_Plan.md Phase 8 + docs/HLD.md §11:

  * report_drainer       — drains orders:reports:pending → Postgres
  * instrument_refresh   — daily NSE master refresh (event-driven)
  * pg_maintenance       — nightly VACUUM ANALYZE
  * kill_switch_poller   — periodic broker kill-switch snapshot
  * log_rotation         — placeholder (real rotation handled by logrotate)

Run:
    python -m engines.background
"""
