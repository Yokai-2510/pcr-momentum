"""rate_limiter — stateless, caller-driven counters."""

from __future__ import annotations

import time

from brokers.upstox.rate_limiter import check_rate_limit, increment_rate_counter


def _new_state() -> dict:
    return {
        "last_request_second": 0,
        "order_requests_this_second": 0,
        "position_requests_this_second": 0,
        "last_order_place": 0.0,
    }


def test_under_limit_returns_true() -> None:
    s = _new_state()
    cfg = {"order_rate_limit": 3}
    for _ in range(3):
        assert check_rate_limit(s, cfg, "order") is True
        increment_rate_counter(s, "order")
    assert check_rate_limit(s, cfg, "order") is False


def test_resets_each_second(monkeypatch) -> None:
    s = _new_state()
    cfg = {"order_rate_limit": 1}
    base = int(time.time())

    monkeypatch.setattr(time, "time", lambda: base)
    assert check_rate_limit(s, cfg, "order") is True
    increment_rate_counter(s, "order")
    assert check_rate_limit(s, cfg, "order") is False

    monkeypatch.setattr(time, "time", lambda: base + 1)
    assert check_rate_limit(s, cfg, "order") is True


def test_position_counter_independent() -> None:
    s = _new_state()
    cfg = {"order_rate_limit": 1, "position_rate_limit": 1}
    # Canonical flow: check (which resets per-second counters) → increment.
    assert check_rate_limit(s, cfg, "order") is True
    increment_rate_counter(s, "order")
    # Order budget spent within this second; position should still have budget.
    assert check_rate_limit(s, cfg, "order") is False
    assert check_rate_limit(s, cfg, "position") is True


def test_unknown_request_type_passes() -> None:
    s = _new_state()
    assert check_rate_limit(s, {}, "weird") is True
