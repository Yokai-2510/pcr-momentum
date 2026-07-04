"""Runner ↔ Strategy protocol wiring (Phase A genericity).

Proves the runner drives ANY Strategy implementation through the component
hooks (create_memory / update_universe / build_snapshot / on_tick /
on_config_reload) without importing concrete strategy code, and that the
bid/ask strategy's own hooks reproduce its previous runner-inlined behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import fakeredis
import orjson
import pytest

from engines.strategy import runner
from engines.strategy.strategies.base import (
    Action,
    ActionKind,
    MarketView,
    UniverseUpdate,
    VesselContext,
)
from engines.strategy.strategies.bid_ask_imbalance.strategy import (
    BidAskImbalanceStrategy,
    MemoryStore,
)
from state import keys as K

# ── A synthetic strategy with its OWN snapshot + memory schema ─────────────


@dataclass(slots=True)
class _DummySnapshot:
    """Deliberately nothing like the bid/ask Snapshot."""

    my_field: float


@dataclass(slots=True)
class _DummyMemory:
    ticks_seen: int = 0
    # VesselMemory protocol attributes:
    last_action_kind: ActionKind | None = None
    held_token: str | None = None
    held_strike: int | None = None
    held_side: str | None = None
    suppress_until_ts: int = 0


class _DummyStrategy:
    """Minimal Strategy protocol implementation with its own internal schema."""

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def create_memory(self, ctx: VesselContext) -> _DummyMemory:
        return _DummyMemory()

    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None:
        if memory.ticks_seen == 0:
            return UniverseUpdate(
                subscribe=("TOKEN_A", "TOKEN_B"),
                basket_view={"tokens": ["TOKEN_A", "TOKEN_B"]},
                reason="initial",
            )
        return None

    def build_snapshot(self, ctx: VesselContext, memory: Any, market: MarketView) -> _DummySnapshot:
        return _DummySnapshot(my_field=float(market.spot.get("ltp") or 0.0))

    def on_tick(self, ctx: VesselContext, snapshot: Any, memory: Any) -> Action:
        memory.ticks_seen += 1
        return Action(
            ActionKind.NO_OP,
            reason="dummy",
            metrics={"my_custom_metric": snapshot.my_field, "ticks": memory.ticks_seen},
        )

    def on_config_reload(self, ctx: VesselContext, memory: Any) -> None:
        return

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None:
        return


def _ctx(sid: str = "dummy_v1", idx: str = "nifty50") -> VesselContext:
    return VesselContext(
        strategy_id=sid, instrument_id=idx, strategy_config={}, instrument_config={}
    )


def _market(spot_ltp: float = 100.0, chain: dict[str, Any] | None = None) -> MarketView:
    chain = chain or {}
    return MarketView(
        chain=chain,
        spot={"ltp": spot_ltp},
        meta={},
        now_ms=1_700_000_000_000,
        token_lookup=runner._build_token_lookup({}, chain),
    )


class TestDummyStrategyThroughHooks:
    """A strategy with a totally different internal schema drives cleanly."""

    def test_hooks_roundtrip(self) -> None:
        strat = _DummyStrategy()
        ctx = _ctx()
        memory = strat.create_memory(ctx)
        market = _market(spot_ltp=123.5)

        update = strat.update_universe(ctx, memory, market)
        assert update is not None
        assert update.subscribe == ("TOKEN_A", "TOKEN_B")

        snapshot = strat.build_snapshot(ctx, memory, market)
        action = strat.on_tick(ctx, snapshot, memory)
        assert action.metrics["my_custom_metric"] == 123.5
        assert memory.ticks_seen == 1
        # Second universe check: strategy says "no change".
        assert strat.update_universe(ctx, memory, market) is None

    async def test_persist_metrics_writes_generic_blob(self) -> None:
        server = fakeredis.FakeServer()
        redis_async = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        action = Action(
            ActionKind.NO_OP,
            reason="dummy",
            metrics={"my_custom_metric": 42.0, "ticks": 7},
        )
        await runner._persist_metrics_and_decision(
            redis_async, sid="dummy_v1", instrument_id="nifty50", action=action
        )
        blob = orjson.loads(await redis_async.get(K.vessel_metrics_latest("dummy_v1", "nifty50")))
        assert blob == {"my_custom_metric": 42.0, "ticks": 7}
        decision = orjson.loads(
            await redis_async.get(K.vessel_metrics_last_decision("dummy_v1", "nifty50"))
        )
        assert decision["action"] == "NO_OP"
        await redis_async.aclose()


class TestBidAskHooksMatchOldRunnerBehavior:
    """The bid/ask strategy's hooks reproduce the previously runner-inlined logic."""

    def test_create_memory_reads_buffer_config(self) -> None:
        strat = BidAskImbalanceStrategy()
        ctx = _ctx(sid="bid_ask_imbalance_v1")
        ctx.strategy_config = {"buffer": {"ring_size": 7}}
        memory = strat.create_memory(ctx)
        assert isinstance(memory, MemoryStore)
        assert memory.buffers.capacity == 7
        assert memory.basket.atm == 0

    def test_update_universe_initial_basket(self) -> None:
        strat = BidAskImbalanceStrategy()
        ctx = _ctx(sid="bid_ask_imbalance_v1")
        ctx.instrument_config = {"strike_step": 50, "basket_size": 1}
        memory = strat.create_memory(ctx)

        chain = {
            "100": {"ce": {"token": "CE100"}, "pe": {"token": "PE100"}},
            "150": {"ce": {"token": "CE150"}, "pe": {"token": "PE150"}},
            "200": {"ce": {"token": "CE200"}, "pe": {"token": "PE200"}},
        }
        update = strat.update_universe(ctx, memory, _market(spot_ltp=150.0, chain=chain))
        assert update is not None
        assert set(update.subscribe) == {"CE100", "CE150", "CE200", "PE100", "PE150", "PE200"}
        assert update.basket_view is not None
        assert update.basket_view["atm"] == 150
        assert memory.basket.atm == 150

        # Same spot again -> no change.
        assert strat.update_universe(ctx, memory, _market(spot_ltp=150.0, chain=chain)) is None

    def test_build_snapshot_pins_held_leg(self) -> None:
        strat = BidAskImbalanceStrategy()
        ctx = _ctx(sid="bid_ask_imbalance_v1")
        ctx.instrument_config = {"strike_step": 50, "basket_size": 1}
        memory = strat.create_memory(ctx)
        chain = {
            "100": {"ce": {"token": "CE100"}, "pe": {"token": "PE100"}},
            "150": {"ce": {"token": "CE150"}, "pe": {"token": "PE150"}},
            "200": {"ce": {"token": "CE200"}, "pe": {"token": "PE200"}},
        }
        market = _market(spot_ltp=150.0, chain=chain)
        strat.update_universe(ctx, memory, market)
        snap = strat.build_snapshot(ctx, memory, market)
        assert snap.instrument_id == "nifty50"
        assert snap.atm == 150


class TestApplyActionGeneric:
    """_apply_action works against the VesselMemory protocol, not MemoryStore."""

    async def test_reversal_warn_sets_suppression_on_dummy_memory(self) -> None:
        from engines.strategy.registry import VesselSpec

        server = fakeredis.FakeServer()
        redis_async = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        redis_sync = fakeredis.FakeRedis(server=server, decode_responses=True)

        strat = _DummyStrategy()
        ctx = _ctx()
        spec = VesselSpec(
            strategy_id="dummy_v1", instrument_id="nifty50", strategy=strat, context=ctx
        )
        memory = strat.create_memory(ctx)
        action = Action(ActionKind.REVERSAL_WARN, reason="warn")
        await runner._apply_action(redis_async, redis_sync, spec=spec, action=action, memory=memory)
        assert memory.suppress_until_ts > 0
        assert memory.last_action_kind == ActionKind.REVERSAL_WARN
        await redis_async.aclose()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
