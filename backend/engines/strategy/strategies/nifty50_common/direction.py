"""Direction prediction — CE or PE. Faithful port of the original rank-momentum
`direction_prediction` module (modes: basic / post_settlement_bias / fixed).

post_settlement_bias:
    Sum CE premiums and PE premiums across `strike_count` strikes in the chosen
    bucket (ATM / OTM / ITM) around spot, compare each sum's % change vs the
    pre-open settlement snapshot (captured ~09:10 IST). If the CE sum rose
    `threshold_pct` more than the PE sum -> BULLISH (buy CE); the reverse ->
    BEARISH (buy PE); otherwise NEUTRAL (resolved by the fallback policy).

Bucket semantics (strikes sorted ascending; ATM = nearest to spot):
    ATM: the single ATM strike (strike_count forced to 1).
    OTM: CE = higher strikes going outward; PE = lower strikes going outward.
    ITM: CE = lower strikes going inward;  PE = higher strikes going inward.

All functions are pure — chain data and the snapshot are passed in; no I/O.
"""

from __future__ import annotations

from typing import Any

Leaf = dict[str, Any]
Chain = dict[int, Leaf]  # strike -> option leaf ({ltp, bid, ask, ts, token, ...})


def find_atm_index(strikes: list[int], spot: float) -> int:
    """Index of the strike nearest to spot in an ascending-sorted list."""
    return min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))


def collect_bucket_strikes(
    chain: Chain,
    spot: float,
    bucket: str,
    strike_count: int,
    option_type: str,
) -> list[tuple[int, Leaf]]:
    """Collect (strike, leaf) tuples for the bucket, mirroring the original
    `_collect_strikes_for_bucket` exactly (incl. CE/PE directionality)."""
    if not chain:
        return []
    strikes = sorted(chain.keys())
    if not strikes:
        return []
    atm_idx = find_atm_index(strikes, spot)

    if bucket == "ATM":
        leaf = chain.get(strikes[atm_idx])
        return [(strikes[atm_idx], leaf)] if leaf is not None else []

    result: list[tuple[int, Leaf]] = []
    if bucket == "OTM":
        if option_type == "CE":  # CE OTM = higher strikes
            rng = range(atm_idx + 1, min(atm_idx + 1 + strike_count, len(strikes)))
        else:  # PE OTM = lower strikes
            rng = range(atm_idx - 1, max(atm_idx - 1 - strike_count, -1), -1)
    elif bucket == "ITM":
        if option_type == "CE":  # CE ITM = lower strikes
            rng = range(atm_idx - 1, max(atm_idx - 1 - strike_count, -1), -1)
        else:  # PE ITM = higher strikes
            rng = range(atm_idx + 1, min(atm_idx + 1 + strike_count, len(strikes)))
    else:
        return []

    for i in rng:
        leaf = chain.get(strikes[i])
        if leaf is not None:
            result.append((strikes[i], leaf))
    return result


def post_settlement_bias(
    *,
    snapshot: dict[str, dict[int, float]] | None,
    ce_chain: Chain,
    pe_chain: Chain,
    spot: float,
    bucket: str = "ITM",
    strike_count: int = 3,
    threshold_pct: float = 0.5,
) -> tuple[str, dict[str, Any]]:
    """Compare CE-sum vs PE-sum % change against the settlement snapshot.

    Returns (direction, calc) where direction is BULLISH/BEARISH/NEUTRAL and
    calc is the full audit trail (identical fields to the original system).
    """
    if not snapshot:
        return "NEUTRAL", {"reason": "no_snapshot"}
    if spot <= 0:
        return "NEUTRAL", {"reason": "no_ltp"}

    effective_count = 1 if bucket == "ATM" else strike_count
    ce_strikes = collect_bucket_strikes(ce_chain, spot, bucket, effective_count, "CE")
    pe_strikes = collect_bucket_strikes(pe_chain, spot, bucket, effective_count, "PE")
    if not ce_strikes or not pe_strikes:
        return "NEUTRAL", {"reason": "empty_strikes", "bucket": bucket}

    snap_ce = snapshot.get("CE", {})
    snap_pe = snapshot.get("PE", {})

    curr_ce_sum = sum(float(leaf.get("ltp") or 0.0) for _, leaf in ce_strikes)
    curr_pe_sum = sum(float(leaf.get("ltp") or 0.0) for _, leaf in pe_strikes)
    snap_ce_sum = sum(snap_ce.get(strike, 0.0) for strike, _ in ce_strikes)
    snap_pe_sum = sum(snap_pe.get(strike, 0.0) for strike, _ in pe_strikes)

    calc: dict[str, Any] = {
        "bucket": bucket,
        "strike_count": effective_count,
        "threshold_pct": threshold_pct,
        "spot_ltp": round(spot, 2),
        "ce_strikes_used": [s for s, _ in ce_strikes],
        "pe_strikes_used": [s for s, _ in pe_strikes],
        "snap_ce_sum": round(snap_ce_sum, 2),
        "snap_pe_sum": round(snap_pe_sum, 2),
        "curr_ce_sum": round(curr_ce_sum, 2),
        "curr_pe_sum": round(curr_pe_sum, 2),
    }

    if snap_ce_sum <= 0 or snap_pe_sum <= 0 or curr_ce_sum <= 0 or curr_pe_sum <= 0:
        calc["reason"] = "zero_sums"
        return "NEUTRAL", calc

    ce_change_pct = ((curr_ce_sum - snap_ce_sum) / snap_ce_sum) * 100
    pe_change_pct = ((curr_pe_sum - snap_pe_sum) / snap_pe_sum) * 100
    diff = ce_change_pct - pe_change_pct

    calc["ce_change_pct"] = round(ce_change_pct, 4)
    calc["pe_change_pct"] = round(pe_change_pct, 4)
    calc["diff"] = round(diff, 4)

    if diff >= threshold_pct:
        return "BULLISH", calc
    if -diff >= threshold_pct:
        return "BEARISH", calc
    return "NEUTRAL", calc


def apply_smoothing(current: str, history: list[str], *, enabled: bool, periods: int) -> str:
    """Majority-vote smoothing over recent bias history (original semantics)."""
    if not enabled:
        return current
    recent = history[-periods:] if len(history) >= periods else []
    if not recent:
        return current
    bull = recent.count("BULLISH")
    bear = recent.count("BEARISH")
    if bull > bear:
        return "BULLISH"
    if bear > bull:
        return "BEARISH"
    return "NEUTRAL"


def resolve_option_type(direction: str, *, category: str, neutral_fallback: str) -> str | None:
    """Map direction -> CE/PE.

    NEUTRAL resolution follows `neutral_fallback`:
        "category" — original behavior: GAINER -> CE, LOSER -> PE
        "CE" / "PE" — force a side
        "skip"      — no trade (returns None)
    """
    if direction == "BULLISH":
        return "CE"
    if direction == "BEARISH":
        return "PE"
    if neutral_fallback == "category":
        return "CE" if category == "GAINER" else "PE"
    if neutral_fallback in ("CE", "PE"):
        return neutral_fallback
    return None  # "skip"


def predict_direction(
    *,
    mode: str,
    category: str,
    fixed_side: str,
    neutral_fallback: str,
    smoothing_enabled: bool,
    smoothing_periods: int,
    bias_history: list[str],
    snapshot: dict[str, dict[int, float]] | None,
    ce_chain: Chain,
    pe_chain: Chain,
    spot: float,
    bias_cfg: dict[str, Any],
) -> tuple[str | None, str, dict[str, Any]]:
    """Full direction pipeline. Returns (option_type|None, direction, bias_details)."""
    bias_details: dict[str, Any] = {"mode": mode}

    if mode == "fixed":
        side = fixed_side if fixed_side in ("CE", "PE") else "CE"
        bias_details["direction_raw"] = "FIXED"
        bias_details["option_type"] = side
        return side, "FIXED", bias_details

    if mode == "post_settlement_bias":
        direction, calc = post_settlement_bias(
            snapshot=snapshot,
            ce_chain=ce_chain,
            pe_chain=pe_chain,
            spot=spot,
            bucket=str(bias_cfg.get("bucket", "ITM")),
            strike_count=int(bias_cfg.get("strike_count", 3)),
            threshold_pct=float(bias_cfg.get("threshold_pct", 0.5)),
        )
        bias_details.update(calc)
    else:  # "basic" — pure category mapping, no market data needed
        direction = "BULLISH" if category == "GAINER" else "BEARISH"

    direction = apply_smoothing(
        direction, bias_history, enabled=smoothing_enabled, periods=smoothing_periods
    )
    option_type = resolve_option_type(
        direction, category=category, neutral_fallback=neutral_fallback
    )
    bias_details["direction_raw"] = direction
    bias_details["option_type"] = option_type
    return option_type, direction, bias_details
