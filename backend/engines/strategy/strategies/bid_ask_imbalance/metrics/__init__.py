"""The 8 atomic metrics (Strategy.md §4).

Each module exports one or two pure functions. No I/O, no Redis, no logging.
Every function takes data + config and returns a number, classification, or
small dataclass. Fully unit-testable in isolation.

  imbalance.py          per-strike bid/ask ratio (§4.1)
  spread.py             best_ask - best_bid + range classification (§4.2)
  ask_wall.py           wall presence + sub-state (HOLDING/ABSORBING/REFRESHING) (§4.3)
  aggressor.py          LTP-vs-bid/ask classification (§4.4)
  tick_speed.py         consecutive upticks/downticks within 1 s (§4.5)
  cumulative.py         cum_CE_imbalance, cum_PE_imbalance (§4.6)
  pressure.py           net pressure = cum_CE - cum_PE (§4.7)
  quality_score.py      0-10 score on 5 conditions x 2 pts each (§4.8)
"""
