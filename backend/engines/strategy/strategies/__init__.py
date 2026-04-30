"""Per-index strategy classes (one thread per index)."""

from __future__ import annotations

from engines.strategy.strategies.banknifty import BANKNIFTYStrategy
from engines.strategy.strategies.base import StrategyInstance
from engines.strategy.strategies.nifty50 import NIFTY50Strategy

__all__ = ["BANKNIFTYStrategy", "NIFTY50Strategy", "StrategyInstance"]
