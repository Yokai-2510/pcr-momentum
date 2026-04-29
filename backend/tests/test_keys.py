"""Tests for `state/keys.py` — canonical Redis key namespace."""

from __future__ import annotations

import pytest

from state import keys


class TestStaticConstants:
    def test_system_flag_keys_are_lowercase_colon(self) -> None:
        assert keys.SYSTEM_FLAGS_READY == "system:flags:ready"
        assert keys.SYSTEM_FLAGS_TRADING_ACTIVE == "system:flags:trading_active"
        assert keys.SYSTEM_FLAGS_TRADING_DISABLED_REASON == ("system:flags:trading_disabled_reason")
        assert keys.SYSTEM_FLAGS_MODE == "system:flags:mode"

    def test_health_keys(self) -> None:
        assert keys.SYSTEM_HEALTH_AUTH == "system:health:auth"
        assert keys.SYSTEM_HEALTH_HEARTBEATS == "system:health:heartbeats"
        assert keys.SYSTEM_HEALTH_DEPENDENCIES == "system:health:dependencies"

    def test_user_keys(self) -> None:
        assert keys.USER_CREDENTIALS_UPSTOX == "user:credentials:upstox"
        assert keys.USER_AUTH_ACCESS_TOKEN == "user:auth:access_token"
        assert keys.USER_CAPITAL_FUNDS == "user:capital:funds"

    def test_orders_keys(self) -> None:
        assert keys.ORDERS_POSITIONS_OPEN == "orders:positions:open"
        assert keys.ORDERS_PNL_DAY == "orders:pnl:day"
        assert keys.ORDERS_STREAM_ORDER_EVENTS == "orders:stream:order_events"


class TestPerIndexHelpers:
    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_strategy_state(self, index: str) -> None:
        assert keys.strategy_state(index) == f"strategy:{index}:state"

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_market_data_chain(self, index: str) -> None:
        assert (
            keys.market_data_index_option_chain(index)
            == f"market_data:indexes:{index}:option_chain"
        )

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_delta_pcr_keys(self, index: str) -> None:
        assert keys.delta_pcr_baseline(index) == f"strategy:{index}:delta_pcr:baseline"
        assert keys.delta_pcr_cumulative(index) == f"strategy:{index}:delta_pcr:cumulative"

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_view_keys(self, index: str) -> None:
        assert keys.ui_view_strategy(index) == f"ui:views:strategy:{index}"
        assert keys.ui_view_position(index) == f"ui:views:position:{index}"
        assert keys.ui_view_delta_pcr(index) == f"ui:views:delta_pcr:{index}"

    def test_invalid_index_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown index"):
            keys.strategy_state("NIFTY50")  # uppercase rejected
        with pytest.raises(ValueError, match="unknown index"):
            keys.market_data_index_meta("sensex")


class TestPosOrderHelpers:
    def test_position_key(self) -> None:
        assert keys.orders_position("abc-123") == "orders:positions:abc-123"

    def test_order_key(self) -> None:
        assert keys.orders_order("BR-1") == "orders:orders:BR-1"

    def test_status_key(self) -> None:
        assert keys.orders_status("p1") == "orders:status:p1"

    def test_signal_key(self) -> None:
        assert keys.strategy_signal("nifty50_1") == "strategy:signals:nifty50_1"


class TestEnumsAndIndexes:
    def test_index_tuple_complete(self) -> None:
        assert set(keys.INDEXES) == {"nifty50", "banknifty"}

    def test_heartbeat_fields_cover_threads(self) -> None:
        # Sanity: contains at least one entry per logical thread group.
        joined = "|".join(keys.HEARTBEAT_FIELDS)
        for needle in ("init", "data_pipeline", "order_exec", "scheduler", "health"):
            assert needle in joined
        # Per-index threads
        for index in keys.INDEXES:
            assert f"strategy:{index}" in joined
            assert f"background:delta_pcr:{index}" in joined
