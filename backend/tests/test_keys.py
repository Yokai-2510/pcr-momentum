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


class TestPerVesselHelpers:
    SID = "bid_ask_imbalance_v1"

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_vessel_state(self, index: str) -> None:
        assert keys.vessel_state(self.SID, index) == f"strategy:{self.SID}:{index}:state"

    def test_vessel_keys_accept_stock_instruments(self) -> None:
        # Vessel namespace is not limited to the market-data index set —
        # stock-universe strategies address vessels like (sid, "reliance").
        assert keys.vessel_state(self.SID, "reliance") == (f"strategy:{self.SID}:reliance:state")

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_market_data_chain(self, index: str) -> None:
        assert (
            keys.market_data_index_option_chain(index)
            == f"market_data:indexes:{index}:option_chain"
        )

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_delta_pcr_keys_are_legacy_namespaced(self, index: str) -> None:
        assert keys.delta_pcr_baseline(index) == f"strategy:legacy:{index}:delta_pcr:baseline"
        assert keys.delta_pcr_cumulative(index) == (f"strategy:legacy:{index}:delta_pcr:cumulative")

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_view_keys(self, index: str) -> None:
        assert keys.ui_view_vessel(self.SID, index) == f"ui:views:vessels:{self.SID}:{index}"
        assert keys.ui_view_position(index) == f"ui:views:position:{index}"
        assert keys.ui_view_delta_pcr(index) == f"ui:views:legacy:delta_pcr:{index}"

    def test_invalid_instrument_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid instrument"):
            keys.vessel_state(self.SID, "NIFTY50")  # uppercase rejected
        with pytest.raises(ValueError, match="invalid instrument"):
            keys.market_data_index_meta("SENSEX-X")  # format-invalid rejected
        # Universe / stock instrument ids are valid market-data owners now.
        assert keys.market_data_index_meta("nifty50_stocks").endswith(":meta")
        assert keys.market_data_index_spot("stk_reliance").endswith(":spot")


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
        assert set(keys.INDEXES) == {"nifty50", "banknifty", "sensex"}

    def test_heartbeat_fields_cover_engines(self) -> None:
        # Sanity: contains at least one entry per logical engine.
        joined = "|".join(keys.HEARTBEAT_FIELDS_STATIC)
        for needle in ("init", "data_pipeline", "order_exec", "scheduler", "health"):
            assert needle in joined
        # Vessel heartbeats are dynamic HASH fields
        assert keys.heartbeat_field_vessel("bid_ask_imbalance_v1", "nifty50") == (
            "strategy:bid_ask_imbalance_v1:nifty50"
        )
