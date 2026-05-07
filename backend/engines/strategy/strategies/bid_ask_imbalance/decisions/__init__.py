"""Decision rules (Strategy.md §5).

Pure functions that take metrics + state and return an Action / boolean
flag / typed enum. No I/O, no side effects.

  entry_gates.py      4-gate sequence (§5.2)
  continuation.py     in-trade hold logic (§5.3)
  reversal.py         reversal warning (4-of-4 conjunction) (§5.4)
  timing.py           time-of-day score thresholds (§6)
"""
