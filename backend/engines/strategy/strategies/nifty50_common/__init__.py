"""nifty50_common — shared building blocks for NIFTY-50 stock-option strategies.

Faithful ports from the original rank-momentum system, consumed by both
`open_gainer_loser` and `leaderboard_overtake`:

    direction.py  — CE/PE prediction (basic / post_settlement_bias / fixed)
    selection.py  — strike selection (ATM/OTM/ITM + offset) + premium filter
    ranking.py    — leaderboard build (validity filters + rank) + rank-1
                    overtake detection + settlement snapshot capture
"""
