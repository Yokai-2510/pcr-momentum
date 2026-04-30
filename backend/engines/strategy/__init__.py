"""
engines.strategy - per-index strategy threads (Strategy.md).

One process; two threads (one per index) running independent state machines
that consume `market_data:stream:tick:{index}` and emit signals to
`strategy:stream:signals` for Order Exec.

Module map:
  - premium_diff.py        - pure helpers (compute_diffs, compute_sums, ...)
  - decision.py            - pure decision functions per state (FLAT, IN_*, COOLDOWN)
  - pre_open_snapshot.py   - 09:14:50 immutable baseline + fail-closed gate
  - pipeline.py            - pre-signal gates (system flags + liquidity)
  - strategies/base.py     - StrategyInstance run loop
  - strategies/{nifty50,banknifty}.py - concrete subclasses
  - main.py                - spawns one thread per enabled index
  - __main__.py            - `python -m engines.strategy`
"""
