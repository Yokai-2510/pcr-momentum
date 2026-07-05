"""Leaderboard ranking + overtake detection. Ports of the original
rank-momentum `leaderboard.py` (filter -> sort -> rank) and
`overtake_tracker.detect_overtakes` (rank-1 change with prior visibility).

Inputs are the universe spot map ({SYMBOL: {ltp, prev_close, change_pct,
volume, ts}}) — pure functions, no I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

SpotMap = dict[str, dict[str, Any]]


def valid_stocks(
    spot: SpotMap,
    *,
    exclude_circuit_limits: bool = True,
    circuit_limit_threshold_pct: float = 20.0,
    exclude_suspended_stocks: bool = True,
) -> list[dict[str, Any]]:
    """Filter out zero-LTP, circuit-limit movers, and suspended (zero-volume)
    stocks — identical to the original `_get_valid_stocks`."""
    valid: list[dict[str, Any]] = []
    for symbol, data in spot.items():
        ltp = float(data.get("ltp") or 0)
        if ltp <= 0:
            continue
        prev_close = float(data.get("prev_close") or 0)
        change_pct = float(data.get("change_pct") or 0)
        if (
            exclude_circuit_limits
            and prev_close > 0
            and abs((ltp - prev_close) / prev_close * 100) >= circuit_limit_threshold_pct
        ):
            continue
        if exclude_suspended_stocks and int(data.get("volume") or 0) == 0:
            continue
        valid.append(
            {
                "symbol": symbol,
                "ltp": ltp,
                "change_pct": change_pct,
                "volume": int(data.get("volume") or 0),
                "prev_close": prev_close,
                "ts": int(data.get("ts") or 0),
            }
        )
    return valid


def rank(stocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (gainers, losers), each ranked 1..N (original `_sort_and_slice`).

    gainers: rank 1 = highest change_pct; losers: rank 1 = lowest change_pct.
    """
    by_change = sorted(stocks, key=lambda x: x["change_pct"], reverse=True)

    def _with_rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "rank": idx} for idx, row in enumerate(rows, start=1)]

    return _with_rank(by_change), _with_rank(by_change[::-1])


def detect_overtake(
    prev_ranking: list[dict[str, Any]],
    curr_ranking: list[dict[str, Any]],
    category: str,
    now_ms: int,
) -> dict[str, Any] | None:
    """Rank-1 overtake per the original semantics: only counts when the new
    rank-1 was previously VISIBLE in the tracked list at a rank > 1."""
    if not prev_ranking or not curr_ranking:
        return None
    prev_rank1 = prev_ranking[0]["symbol"]
    curr_rank1 = curr_ranking[0]["symbol"]
    if prev_rank1 == curr_rank1:
        return None
    prev_rank_of_new = next(
        (item["rank"] for item in prev_ranking if item["symbol"] == curr_rank1), None
    )
    if prev_rank_of_new is None or prev_rank_of_new <= 1:
        return None
    return {
        "timestamp_ms": now_ms,
        "category": category,
        "symbol": curr_rank1,
        "new_rank": 1,
        "old_rank": prev_rank_of_new,
        "change_pct": curr_ranking[0]["change_pct"],
        "ltp": curr_ranking[0]["ltp"],
        "previous_rank_1": prev_rank1,
    }


def split_platform_chain(
    chain: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Platform chain {strike_str: {ce: leaf, pe: leaf}} -> (ce_map, pe_map)."""
    ce: dict[int, dict[str, Any]] = {}
    pe: dict[int, dict[str, Any]] = {}
    for strike_raw, sides in (chain or {}).items():
        if not isinstance(sides, dict):
            continue
        try:
            strike = int(strike_raw)
        except (TypeError, ValueError):
            continue
        ce_leaf = sides.get("ce")
        pe_leaf = sides.get("pe")
        if isinstance(ce_leaf, dict):
            ce[strike] = ce_leaf
        if isinstance(pe_leaf, dict):
            pe[strike] = pe_leaf
    return ce, pe


def capture_premium_snapshots(
    symbols: list[str],
    read_chain: Callable[[str], dict[str, Any]],
) -> dict[str, dict[str, dict[int, float]]]:
    """Per-symbol {SYM: {"CE": {strike: ltp}, "PE": {strike: ltp}}} with live
    premiums only — the ~09:10 settlement baseline for post_settlement_bias."""
    out: dict[str, dict[str, dict[int, float]]] = {}
    for symbol in symbols:
        ce, pe = split_platform_chain(read_chain(symbol))
        ce_snap = {s: float(leaf.get("ltp") or 0) for s, leaf in ce.items()}
        pe_snap = {s: float(leaf.get("ltp") or 0) for s, leaf in pe.items()}
        ce_snap = {s: v for s, v in ce_snap.items() if v > 0}
        pe_snap = {s: v for s, v in pe_snap.items() if v > 0}
        if ce_snap and pe_snap:
            out[symbol] = {"CE": ce_snap, "PE": pe_snap}
    return out
