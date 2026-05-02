"""Health engine — periodic probes of every dependency the stack relies on.

Per docs/Project_Plan.md Phase 8 + docs/HLD.md §11.

Probes (run every `HEALTH_PROBE_INTERVAL_SEC`, default 10):
  * redis           — PING the live socket
  * postgres        — SELECT 1
  * broker_rest     — UpstoxAPI.get_market_status (no auth required)
  * broker_ws       — recency of `market_data:ws_status:market_ws`
  * system_load     — psutil 1-min loadavg
  * swap_usage      — psutil swap percent
  * engines         — recency of `system:health:heartbeats.{engine}`

Outputs:
  system:health:summary       (HASH: status, ts_ms)
  system:health:dependencies  (HASH: per-dep status JSON)
  system:health:engines       (HASH: per-engine status JSON)
  system:health:alerts        (STREAM: appended on red transitions)

Run:
    python -m engines.health
"""
