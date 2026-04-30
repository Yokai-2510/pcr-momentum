"""engines.strategy.strategies.nifty50 - concrete subclass."""

from __future__ import annotations

from engines.strategy.strategies.base import StrategyInstance


class NIFTY50Strategy(StrategyInstance):
    index = "nifty50"
