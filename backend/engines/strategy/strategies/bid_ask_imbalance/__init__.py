"""Bid/Ask Imbalance order-flow strategy.

Strategy.md is the spec. Module map:

  strategy.py           main orchestrator (entry point for the runner)
  basket.py             dynamic ATM + basket management (Strategy.md §3)
  snapshot.py           typed view of basket-token state at one instant
  buffer.py             per-strike rolling tick history (in-memory)
  state.py              vessel state machine helpers

  metrics/              the 8 atomic metrics (Strategy.md §4)
  decisions/            entry gates, continuation, reversal, timing (§5–§6)
"""

from engines.strategy.strategies.bid_ask_imbalance.strategy import (
    BidAskImbalanceStrategy,
)

STRATEGY_ID = "bid_ask_imbalance_v1"

__all__ = ["BidAskImbalanceStrategy", "STRATEGY_ID"]
