"""Scheduler engine — APScheduler-driven daily lifecycle triggers.

Per docs/Project_Plan.md Phase 8 + docs/Sequential_Flow.md.

Cron jobs (IST) — defaults from `strategy:configs:session`:
  05:30  instrument_refresh
  09:14  pre_open_snapshot
  09:15  market_open
  15:10  entry_freeze
  15:15  eod_squareoff
  15:30  market_close
  15:35  daily_reset
  02:00  nightly_maintenance

Each fires a payload onto `system:stream:scheduler_events` and may also
flip a flag on Redis directly (e.g. trading_active, entry_freeze). Engines
that care subscribe to the stream.

Run:
    python -m engines.scheduler
"""
