"""
engines.strategy.strategies.base — abstract Strategy interface.

A `Strategy` is a stateless-ish algorithm: given a `Snapshot` of current
market state plus a per-vessel `MemoryStore` (rolling buffers, last decision,
current position info), it returns an `Action`.

The runner (`engines.strategy.runner.Vessel`) owns all I/O. The strategy
itself is a pure function of (snapshot, memory) -> action. This separation:

  - makes every strategy unit-testable without Redis or broker
  - keeps the runner reusable across all strategies
  - means a new strategy is one new directory, not engine-wide changes
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol


class ActionKind(str, enum.Enum):
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

    Mutable state lives in the runner's memory store. The context is the
    "who am I" of this run; memory is the "what I remember".
    """

    strategy_id: str
    instrument_id: str
    strategy_config: dict[str, Any]      # strategy:configs:strategies:{sid}
    instrument_config: dict[str, Any]    # strategy:configs:strategies:{sid}:instruments:{idx}


class Strategy(Protocol):
    """Every strategy under `strategies/` MUST implement this Protocol.

    The runner instantiates one Strategy per vessel and calls:
        - prepare(ctx, **kwargs)        # once at vessel BOOT
        - on_pre_open(ctx, **kwargs)    # at 09:14:50 IST (optional)
        - on_tick(ctx, snapshot, mem)   # every tick during LIVE
        - on_drain(ctx, **kwargs)       # at 15:25 IST
    """

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None: ...

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None: ...

    def on_tick(
        self,
        ctx: VesselContext,
        snapshot: Any,
        memory: Any,
    ) -> Action: ...

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None: ...
