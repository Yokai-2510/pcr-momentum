"""
engines.strategy.strategies.base — abstract Strategy interface.

A `Strategy` is a stateless-ish algorithm: given a strategy-defined snapshot
of current market state plus a per-vessel memory object (rolling buffers,
last decision, current position info), it returns an `Action`.

The runner (`engines.strategy.runner`) owns all I/O. The strategy itself is
pure compute over data the runner hands it. This separation:

  - makes every strategy unit-testable without Redis or broker
  - keeps the runner reusable across all strategies
  - means a new strategy is one new directory, not engine-wide changes

Each strategy owns its OWN internal schema: its snapshot type, its memory
type, its metric names, its subscription policy. The only shared contracts
are the small dataclasses below (Action / MarketView / UniverseUpdate) and
the `VesselMemory` attribute protocol the runner needs for position sync.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ActionKind(enum.StrEnum):
    NO_OP = "NO_OP"
    ENTER = "ENTER"
    HOLD = "HOLD"
    EXIT = "EXIT"
    FLIP = "FLIP"
    REVERSAL_WARN = "REVERSAL_WARN"  # informational; no order side-effect


@dataclass(slots=True, frozen=True)
class Action:
    """The output of every strategy evaluation. One per tick.

    Even NO_OP actions are written to redis as the last-decision telemetry
    (Strategy.md §11.1) — this is what makes the silent-loop bug class
    architecturally impossible.

    `metrics` is strategy-defined: each strategy publishes its own metric
    names (no central schema). Numeric entries are forwarded into the
    Signal's `metrics_at_signal` blob for forensics/attribution.
    """

    kind: ActionKind
    side: str | None = None  # "CE" | "PE" | None
    strike: int | None = None
    instrument_token: str | None = None
    qty_lots: int | None = None
    score: float | None = None
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    score_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class VesselContext:
    """Identity + immutable per-vessel context handed to every strategy call.

    Mutable state lives in the strategy's memory object. The context is the
    "who am I" of this run; memory is the "what I remember".
    """

    strategy_id: str
    instrument_id: str
    strategy_config: dict[str, Any]  # strategy:configs:strategies:{sid}
    instrument_config: dict[str, Any]  # strategy:configs:strategies:{sid}:instruments:{idx}


@dataclass(slots=True)
class MarketView:
    """Read-only view of current market state, built by the runner per tick.

    Everything a strategy's component hooks (build_snapshot / update_universe)
    may need, pre-fetched so the hooks stay pure (no Redis inside strategies).
    """

    chain: dict[str, Any]  # option_chain: strike -> {ce: {...}, pe: {...}}
    spot: dict[str, Any]  # spot hash (ltp, prev_close, ...)
    meta: dict[str, Any]  # market_data:{idx}:meta
    now_ms: int
    token_lookup: Callable[[int, str], str | None]  # (strike, "CE"/"PE") -> token


@dataclass(slots=True, frozen=True)
class UniverseUpdate:
    """A strategy's requested change to its subscribed instrument set.

    Returned by `Strategy.update_universe` when the working set should shift
    (e.g. ATM moved and the strike basket follows). The runner applies it:
    subscribes/unsubscribes tokens, re-routes tick events, and persists
    `basket_view` (if given) to the vessel's basket key for observability.
    """

    subscribe: tuple[str, ...] = ()
    unsubscribe: tuple[str, ...] = ()
    basket_view: dict[str, Any] | None = None  # persisted to strategy:{sid}:{idx}:basket
    reason: str = ""


class VesselMemory(Protocol):
    """Attributes the RUNNER needs on every strategy's memory object.

    Strategies define their own memory dataclass with whatever internal
    fields they want (buffers, baskets, rolling windows, ...) — the runner
    only touches these five, for position sync and suppression handling.
    """

    last_action_kind: ActionKind | None
    held_token: str | None
    held_strike: int | None
    held_side: str | None
    suppress_until_ts: int


class Strategy(Protocol):
    """Every strategy under `strategies/` MUST implement this Protocol.

    The runner instantiates one Strategy per vessel and calls:

        Lifecycle:
        - prepare(ctx, **kwargs)          once at vessel BOOT
        - on_pre_open(ctx, **kwargs)      at 09:14:50 IST (optional)
        - on_drain(ctx, **kwargs)         at session close

        Components (each strategy owns its own):
        - create_memory(ctx)              build the strategy's memory object
        - update_universe(ctx, mem, mkt)  subscription policy (basket shifts);
                                          return None when nothing changes
        - build_snapshot(ctx, mem, mkt)   build the strategy's own snapshot type
        - on_tick(ctx, snapshot, mem)     the decision function -> Action
        - on_config_reload(ctx, mem)      re-derive memory bits from hot config

    Snapshot and memory types are strategy-defined (`Any` here on purpose —
    there is deliberately NO central snapshot schema).
    """

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None: ...

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None: ...

    def create_memory(self, ctx: VesselContext) -> Any: ...

    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None: ...

    def build_snapshot(self, ctx: VesselContext, memory: Any, market: MarketView) -> Any: ...

    def on_tick(
        self,
        ctx: VesselContext,
        snapshot: Any,
        memory: Any,
    ) -> Action: ...

    def on_config_reload(self, ctx: VesselContext, memory: Any) -> None: ...

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None: ...
