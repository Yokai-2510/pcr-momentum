"""leaderboard_overtake — trade NIFTY-50 rank-1 leaderboard overtakes."""

from engines.strategy.strategies.leaderboard_overtake.strategy import (
    LeaderboardOvertakeMemory,
    LeaderboardOvertakeStrategy,
)

STRATEGY_ID = "leaderboard_overtake_v1"
STRATEGY_NAME = "Leaderboard Overtake"
STRATEGY_DESCRIPTION = (
    "Continuously ranks all NIFTY-50 stocks by % change; when a new symbol "
    "overtakes rank 1 on the gainer or loser leaderboard, buys its CE "
    "(gainer) or PE (loser) via stock options. Churn/pair-flip filters, "
    "% change threshold, premium-bias direction and premium range gates "
    "ported from the original rank-momentum system."
)

__all__ = [
    "STRATEGY_DESCRIPTION",
    "STRATEGY_ID",
    "STRATEGY_NAME",
    "LeaderboardOvertakeMemory",
    "LeaderboardOvertakeStrategy",
]
