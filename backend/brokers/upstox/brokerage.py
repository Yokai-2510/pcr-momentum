"""
brokers.upstox.brokerage — GET /v2/charges/brokerage.

Returns the broker's charges + tax breakdown plus a derived `net_value`
(= gross - charges.total) so callers can compute net trade PnL by
subtracting BUY net_value from SELL net_value.
"""

from __future__ import annotations

from typing import Any

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

_BROKERAGE_URL = "https://api.upstox.com/v2/charges/brokerage"

VALID_PRODUCTS = {"D", "I", "MTF"}
VALID_TRANSACTION_TYPES = {"BUY", "SELL"}


def _normalize(parsed: dict[str, Any], qty: int, price: float) -> dict[str, Any]:
    charges = (parsed.get("data") or {}).get("charges") or {}
    total = float(charges.get("total") or 0.0)
    gross = qty * price
    return {
        "charges": charges,
        "gross": round(gross, 4),
        "total": round(total, 4),
        "net_value": round(gross - total, 4),
    }


def get_brokerage(
    instrument_token: str,
    quantity: int,
    product: str,
    transaction_type: str,
    price: float,
    access_token: str,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    if quantity <= 0 or price <= 0:
        return fail(f"invalid_qty_or_price: qty={quantity}, price={price}")
    if product not in VALID_PRODUCTS:
        return fail(f"invalid_product: {product}")
    if transaction_type not in VALID_TRANSACTION_TYPES:
        return fail(f"invalid_transaction_type: {transaction_type}")

    fetch_url = url or _BROKERAGE_URL
    params = {
        "instrument_token": instrument_token,
        "quantity": str(quantity),
        "product": product,
        "transaction_type": transaction_type,
        "price": str(price),
    }
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
        return ok(_normalize(parsed, quantity, price), code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )
