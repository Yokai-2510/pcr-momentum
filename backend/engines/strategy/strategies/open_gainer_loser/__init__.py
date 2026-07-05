"""open_gainer_loser — buy the open's top NIFTY-50 mover, both sides."""

from engines.strategy.strategies.open_gainer_loser.strategy import (
    OpenGainerLoserMemory,
    OpenGainerLoserStrategy,
    UniverseView,
)

STRATEGY_ID = "open_gainer_loser_v1"
STRATEGY_NAME = "Open Gainer-Loser"
STRATEGY_DESCRIPTION = (
    "At market open (09:15 + 60s window), buys the top NIFTY-50 gainer's CE "
    "and the top loser's PE via stock options. Direction confirmed by "
    "post-settlement premium bias vs the 09:10 snapshot; strike from "
    "ITM/ATM/OTM + offset; fires once per side per session."
)

__all__ = [
    "STRATEGY_DESCRIPTION",
    "STRATEGY_ID",
    "STRATEGY_NAME",
    "OpenGainerLoserMemory",
    "OpenGainerLoserStrategy",
    "UniverseView",
]
