"""
brokers.upstox.orders — full order lifecycle (place / modify / cancel) +
all read-side endpoints (status, history, book, trades, exit-all).

Writes use the v3 HFT host (api-hft.upstox.com); reads use v2 (api.upstox.com).
All functions are STATELESS — `access_token` is passed per call. Optional
X-Algo-Name header is supported on writes via `algo_name`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from brokers.upstox._http import bearer_json
from brokers.upstox._http import request as _req
from brokers.upstox.envelopes import fail, ok

# v3 HFT writes
_ORDER_PLACE_URL = "https://api-hft.upstox.com/v3/order/place"
_ORDER_MODIFY_URL = "https://api-hft.upstox.com/v3/order/modify"
_ORDER_CANCEL_URL = "https://api-hft.upstox.com/v3/order/cancel"

# v2 reads
_ORDER_DETAILS_URL = "https://api.upstox.com/v2/order/details"
_ORDER_HISTORY_URL = "https://api.upstox.com/v2/order/history"
_ORDER_BOOK_URL = "https://api.upstox.com/v2/order/retrieve-all"
_TRADES_FOR_DAY_URL = "https://api.upstox.com/v2/order/trades/get-trades-for-day"
_TRADES_BY_ORDER_URL = "https://api.upstox.com/v2/order/trades"
_EXIT_ALL_URL = "https://api.upstox.com/v2/order/positions/exit"

_IST = ZoneInfo("Asia/Kolkata")

VALID_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
VALID_TRANSACTION_TYPES = {"BUY", "SELL"}
VALID_PRODUCTS = {"I", "D", "MTF"}
VALID_VALIDITY = {"DAY", "IOC"}


def _algo_headers(access_token: str, algo_name: str | None) -> dict[str, str]:
    h = bearer_json(access_token, v=2)
    if algo_name:
        h["X-Algo-Name"] = algo_name
    return h


# ── Writes (v3 HFT) ─────────────────────────────────────────────────────


def place_order(
    instrument_token: str,
    quantity: int,
    transaction_type: str,
    access_token: str,
    price: float = 0.0,
    order_type: str = "LIMIT",
    product: str = "D",
    validity: str = "DAY",
    tag: str | None = None,
    disclosed_quantity: int = 0,
    trigger_price: float = 0.0,
    is_amo: bool = False,
    slice: bool = True,
    market_protection: int = 0,
    algo_name: str | None = None,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    if quantity <= 0:
        return fail(f"invalid_quantity: {quantity}")
    if transaction_type not in VALID_TRANSACTION_TYPES:
        return fail(f"invalid_transaction_type: {transaction_type}")
    if order_type not in VALID_ORDER_TYPES:
        return fail(f"invalid_order_type: {order_type}")
    if product not in VALID_PRODUCTS:
        return fail(f"invalid_product: {product}")
    if validity not in VALID_VALIDITY:
        return fail(f"invalid_validity: {validity}")

    payload: dict[str, Any] = {
        "instrument_token": instrument_token,
        "quantity": quantity,
        "product": product,
        "validity": validity,
        "price": price,
        "order_type": order_type,
        "transaction_type": transaction_type,
        "disclosed_quantity": disclosed_quantity,
        "trigger_price": trigger_price,
        "is_amo": is_amo,
        "slice": slice,
    }
    if tag is not None:
        payload["tag"] = tag
    if market_protection is not None and market_protection >= 0:
        payload["market_protection"] = market_protection

    fetch_url = url or _ORDER_PLACE_URL
    try:
        code, parsed, text, _ = _req(
            "POST",
            fetch_url,
            headers=_algo_headers(access_token, algo_name),
            json=payload,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        ids = list((parsed.get("data") or {}).get("order_ids") or [])
        return ok(
            {
                "order_ids": ids,
                "first_order_id": ids[0] if ids else None,
                "latency_ms": (parsed.get("metadata") or {}).get("latency"),
            },
            code=code,
            raw=parsed,
        )
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def modify_order(
    order_id: str,
    access_token: str,
    quantity: int | None = None,
    price: float | None = None,
    order_type: str | None = None,
    validity: str | None = None,
    disclosed_quantity: int | None = None,
    trigger_price: float | None = None,
    market_protection: int | None = None,
    algo_name: str | None = None,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    if order_type is not None and order_type not in VALID_ORDER_TYPES:
        return fail(f"invalid_order_type: {order_type}")
    if validity is not None and validity not in VALID_VALIDITY:
        return fail(f"invalid_validity: {validity}")

    payload: dict[str, Any] = {"order_id": order_id}
    if quantity is not None:
        payload["quantity"] = quantity
    if price is not None:
        payload["price"] = price
    if order_type is not None:
        payload["order_type"] = order_type
    if validity is not None:
        payload["validity"] = validity
    if disclosed_quantity is not None:
        payload["disclosed_quantity"] = disclosed_quantity
    if trigger_price is not None:
        payload["trigger_price"] = trigger_price
    if market_protection is not None:
        payload["market_protection"] = market_protection

    fetch_url = url or _ORDER_MODIFY_URL
    try:
        code, parsed, text, _ = _req(
            "PUT",
            fetch_url,
            headers=_algo_headers(access_token, algo_name),
            json=payload,
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(
            {
                "order_id": (parsed.get("data") or {}).get("order_id"),
                "latency_ms": (parsed.get("metadata") or {}).get("latency"),
            },
            code=code,
            raw=parsed,
        )
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def cancel_order(
    order_id: str,
    access_token: str,
    algo_name: str | None = None,
    timeout: int = 10,
    url: str | None = None,
) -> dict[str, Any]:
    fetch_url = url or _ORDER_CANCEL_URL
    try:
        code, parsed, text, _ = _req(
            "DELETE",
            fetch_url,
            headers=_algo_headers(access_token, algo_name),
            params={"order_id": order_id},
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(
            {
                "order_id": (parsed.get("data") or {}).get("order_id"),
                "latency_ms": (parsed.get("metadata") or {}).get("latency"),
            },
            code=code,
            raw=parsed,
        )
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


# ── Reads (v2) ──────────────────────────────────────────────────────────


def _read(
    url: str, params: dict[str, str] | None, access_token: str, timeout: int
) -> dict[str, Any]:
    try:
        code, parsed, text, _ = _req(
            "GET", url, headers=bearer_json(access_token, v=2), params=params, timeout=timeout
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        data = parsed.get("data")
        if data is None:
            data = []
        return ok(data, code=code, raw=parsed)
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def get_order_status(
    order_id: str, access_token: str, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    return _read(url or _ORDER_DETAILS_URL, {"order_id": order_id}, access_token, timeout)


def get_order_history(
    order_id: str, access_token: str, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    return _read(url or _ORDER_HISTORY_URL, {"order_id": order_id}, access_token, timeout)


def get_order_book(access_token: str, timeout: int = 10, url: str | None = None) -> dict[str, Any]:
    fetch_url = url or _ORDER_BOOK_URL
    try:
        code, parsed, text, _ = _req(
            "GET", fetch_url, headers=bearer_json(access_token, v=2), timeout=timeout
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code != 200:
        return fail(
            f"HTTP {code}: {text}",
            code=code,
            raw=parsed if isinstance(parsed, dict) else None,
        )
    # Tolerate both shapes: bare list OR {status, data}
    if isinstance(parsed, list):
        return ok(parsed, code=code, raw={"_list": parsed})
    if isinstance(parsed, dict) and parsed.get("status") == "success":
        return ok(parsed.get("data") or [], code=code, raw=parsed)
    return fail(
        f"unexpected_shape: {parsed}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def get_trades_for_day(
    access_token: str, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    return _read(url or _TRADES_FOR_DAY_URL, None, access_token, timeout)


def get_trades_by_order(
    order_id: str, access_token: str, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    return _read(url or _TRADES_BY_ORDER_URL, {"order_id": order_id}, access_token, timeout)


def exit_all_positions(
    access_token: str, timeout: int = 10, url: str | None = None
) -> dict[str, Any]:
    fetch_url = url or _EXIT_ALL_URL
    try:
        code, parsed, text, _ = _req(
            "POST",
            fetch_url,
            headers=bearer_json(access_token, v=2),
            json={},
            timeout=timeout,
        )
    except Exception as e:
        return fail(f"REQUEST_EXCEPTION: {e}")
    if code == 200 and isinstance(parsed, dict) and parsed.get("status") == "success":
        summary = parsed.get("summary") or {}
        return ok(
            {
                "order_ids": list((parsed.get("data") or {}).get("order_ids") or []),
                "total": summary.get("total", 0),
                "successful": summary.get("success", 0),
                "errors": summary.get("error", 0),
            },
            code=code,
            raw=parsed,
        )
    return fail(
        f"HTTP {code}: {parsed if parsed is not None else text}",
        code=code,
        raw=parsed if isinstance(parsed, dict) else None,
    )


# ── Audit log helper ────────────────────────────────────────────────────


def save_api_log(
    api_logs_path: str, log_type: str, response: dict[str, Any], identifier: str
) -> None:
    """Persist a response payload to disk for forensic analysis."""
    try:
        logs_dir = Path(api_logs_path)
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = (
            "positions_sync_LATEST.json"
            if log_type == "positions_sync"
            else f"{log_type}_{identifier}_{ts}.json"
        )
        entry = {
            "log_type": log_type,
            "identifier": identifier,
            "timestamp_epoch": time.time(),
            "timestamp_ist": datetime.now(_IST).isoformat(timespec="milliseconds"),
            "response": response,
        }
        with open(logs_dir / filename, "w") as f:
            json.dump(entry, f, indent=2)
    except Exception as e:
        # Best-effort: never raise out of an audit write
        print(f"[orders] save_api_log error: {e}")
