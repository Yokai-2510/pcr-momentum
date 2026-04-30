"""
engines.order_exec — sole writer of broker order state.

Per Strategy.md §10 + TDD §6. The engine consumes signals from
`strategy:stream:signals`, runs each through six stages, persists a closed-
position report, and atomically cleans up Redis.

Stage map (Strategy.md §10):
  A  pre_entry_gate.check          system flags + spread + depth + allocator
  B  entry.submit_and_monitor      DAY LIMIT submit + drift-aware modify
  C  entry monitor                 (folded into B)
  D  exit_eval.evaluate            8-trigger priority cascade
  E  exit_submit.submit_and_complete  modify-only SELL loop (never abandons)
  F  reporting + cleanup           ClosedPositionReport → Postgres → Lua cleanup

Modes:
  paper — orders are simulated against live ticks (no broker call).
  live  — orders go through UpstoxAPI; portfolio WS drives state transitions.

Single-writer rule:
  Order Exec is the only writer of `orders:positions:{pos_id}`,
  `orders:status:{pos_id}`, `orders:orders:{order_id}`,
  and the `orders:positions:open*` membership sets.
"""
