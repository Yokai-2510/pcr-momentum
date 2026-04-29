"""
brokers.upstox.rate_limiter — per-second API rate-limit helpers.

Stateless, caller-driven. The `api_state` dict is provided by the caller
(e.g. Order Exec engine). Counters auto-reset every second.

Standard Upstox limits (as of 2024):
  Orders    : 20 req/s
  Positions : 20 req/s

Flow:
  1. check_rate_limit(api_state, api_cfg, "order")  → bool
  2. if True, perform the API call, then
     increment_rate_counter(api_state, "order")
"""

from __future__ import annotations

import time
from typing import Any


def check_rate_limit(api_state: dict[str, Any], api_cfg: dict[str, Any], request_type: str) -> bool:
    current_second = int(time.time())
    if api_state.get("last_request_second") != current_second:
        api_state["order_requests_this_second"] = 0
        api_state["position_requests_this_second"] = 0
        api_state["last_request_second"] = current_second

    if request_type == "order":
        return bool(
            api_state["order_requests_this_second"] < int(api_cfg.get("order_rate_limit", 20))
        )
    if request_type == "position":
        return bool(
            api_state["position_requests_this_second"] < int(api_cfg.get("position_rate_limit", 20))
        )
    return True


def increment_rate_counter(api_state: dict[str, Any], request_type: str) -> None:
    if request_type == "order":
        api_state["order_requests_this_second"] = api_state.get("order_requests_this_second", 0) + 1
        api_state["last_order_place"] = time.time()
    elif request_type == "position":
        api_state["position_requests_this_second"] = (
            api_state.get("position_requests_this_second", 0) + 1
        )
