"""Tests for `state/streams.py` — stream constants + helpers."""

from __future__ import annotations

import pytest

from state import streams


class TestConstants:
    def test_stream_names_are_canonical(self) -> None:
        assert streams.STREAM_STRATEGY_SIGNALS == "strategy:stream:signals"
        assert streams.STREAM_ORDERS_ORDER_EVENTS == "orders:stream:order_events"
        assert streams.STREAM_UI_HEALTH_ALERTS == "ui:stream:health_alerts"
        assert streams.STREAM_SYSTEM_CONTROL == "system:stream:control"

    def test_pubsub_channels(self) -> None:
        assert streams.PUB_SYSTEM_EVENT == "system:pub:system_event"
        assert streams.PUB_UI_VIEW == "ui:pub:view"

    def test_groups_are_distinct(self) -> None:
        assert (
            streams.GROUP_STRATEGY_NIFTY50
            != streams.GROUP_STRATEGY_BANKNIFTY
            != streams.GROUP_ORDER_EXEC
        )


class TestHelpers:
    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_market_data_tick_stream(self, index: str) -> None:
        assert streams.market_data_tick_stream(index) == f"market_data:stream:tick:{index}"

    def test_market_data_tick_stream_invalid(self) -> None:
        with pytest.raises(ValueError):
            streams.market_data_tick_stream("sensex")

    @pytest.mark.parametrize("index", ["nifty50", "banknifty"])
    def test_strategy_group_for(self, index: str) -> None:
        assert streams.strategy_group_for(index) == f"strategy:{index}"


class TestEnsureConsumerGroupAndIO:
    async def test_create_group_then_read(
        self,
        fake_redis_async: object,  # type: ignore[type-arg]
    ) -> None:
        r = fake_redis_async
        stream = "test:stream:demo"
        group = "g1"

        await streams.ensure_consumer_group(r, stream, group, start_id="0")
        # Re-creating must be idempotent
        await streams.ensure_consumer_group(r, stream, group, start_id="0")

        await r.xadd(stream, {"k": "v"})
        result = await streams.xreadgroup_one(r, stream, group, "c1", block_ms=0)
        assert result is not None
        entry_id, fields = result
        assert isinstance(entry_id, str)
        assert fields == {"k": "v"}

        acked = await streams.ack(r, stream, group, entry_id)
        assert acked == 1

    async def test_xreadgroup_one_returns_none_on_timeout(self, fake_redis_async: object) -> None:
        r = fake_redis_async
        stream = "test:stream:empty"
        group = "g1"

        await streams.ensure_consumer_group(r, stream, group, start_id="0")
        result = await streams.xreadgroup_one(r, stream, group, "c1", block_ms=10)
        assert result is None
