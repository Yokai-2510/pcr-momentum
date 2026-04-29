"""
brokers.upstox.option_chain — GET /v2/option/chain.

Strike-wise CE/PE snapshot with market data + greeks + per-strike PCR.
Used by Background to capture the ΔPCR baseline at 09:14:50.

Pure helpers:
  total_pcr(chain)                       — sum(put OI) / sum(call OI)
  strikes_around_atm(chain, spot, n, …)  — slice to ±N strikes around ATM
"""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_OPTION_CHAIN_URL = "https://api.upstox.com/v2/option/chain"


def get_option_chain(
    instrument_key: str,
    expiry_date: str,
    access_token: str,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    fetch_url = url or _OPTION_CHAIN_URL
    params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
    try:
        code, parsed, text, _ = _req(
            "GET",
            fetch_url,
            headers=bearer_json(access_token, v=2),
            params=params,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        entries = parsed.get("data") or []
        entries = sorted(entries, key=lambda x: x.get("strike_price") or 0)
        return ok(entries, code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def total_pcr(chain: list[dict[str, Any]] | None) -> float | None:
    if not chain:
        return None
    sum_put = 0
    sum_call = 0
    for row in chain:
        co = (row.get("call_options") or {}).get("market_data") or {}
        po = (row.get("put_options") or {}).get("market_data") or {}
        sum_call += int(co.get("oi") or 0)
        sum_put += int(po.get("oi") or 0)
    if sum_call <= 0:
        return None
    return round(sum_put / sum_call, 4)


def strikes_around_atm(
    chain: list[dict[str, Any]] | None,
    spot: float | None = None,
    n_each_side: int = 6,
    strike_step: float | None = None,
) -> list[dict[str, Any]]:
    if not chain:
        return []
    if spot is None:
        spot = chain[0].get("underlying_spot_price")
    if spot is None:
        return []
    atm_idx = min(
        range(len(chain)),
        key=lambda i: abs((chain[i].get("strike_price") or 0) - spot),
    )
    lo = max(0, atm_idx - n_each_side)
    hi = min(len(chain), atm_idx + n_each_side + 1)
    return chain[lo:hi]
