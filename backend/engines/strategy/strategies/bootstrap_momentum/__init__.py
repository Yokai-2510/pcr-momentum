"""bootstrap_momentum — market-open one-shot entry strategy.

Ported from the original rank-momentum system's `bootstrap_orders` pipeline
(direction_prediction + instrument_selection + entry_filters + exit_conditions).
Fires once per session per vessel, within a bounded window after market open.
"""

from engines.strategy.strategies.bootstrap_momentum.strategy import (
    BootstrapMemory,
    BootstrapMomentumStrategy,
    BootstrapView,
)

STRATEGY_ID = "bootstrap_momentum_v1"

__all__ = [
    "STRATEGY_ID",
    "BootstrapMemory",
    "BootstrapMomentumStrategy",
    "BootstrapView",
]
