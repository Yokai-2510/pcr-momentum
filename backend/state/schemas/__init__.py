"""Pydantic v2 models for every cross-engine payload.

Single source of truth: `docs/Schema.md` §5.

Each module in this package owns the models for one logical surface:

- `signal`        — Strategy → Order Exec
- `order_event`   — broker → Background → Order Exec / FastAPI
- `position`      — Order Exec position state machine
- `pnl`           — Background PnL snapshots
- `report`        — closed-position reports persisted to Postgres
- `health`        — Health engine summary + per-dependency probe
- `delta_pcr`     — Background ΔPCR thread output
- `view`          — Frontend view payloads
- `config`        — Runtime configs (execution / session / risk / index)
- `instruments`   — Broker instrument catalog rows
"""

from state.schemas.config import ExecutionConfig, IndexConfig, RiskConfig, SessionConfig
from state.schemas.delta_pcr import DeltaPCRCumulative, DeltaPCRHistoryEntry, DeltaPCRInterval
from state.schemas.health import DependencyStatus, EngineStatus, HealthSummary
from state.schemas.instruments import IndexMeta, OptionChainEntry, OptionContract
from state.schemas.order_event import OrderEvent, OrderEventType
from state.schemas.pnl import DayPnL, PerIndexPnL
from state.schemas.position import ExitProfile, ExitReason, Position, PositionStage
from state.schemas.report import (
    ClosedPositionReport,
    Latencies,
    MarketSnapshot,
    OrderEventEntry,
    PnLBreakdown,
)
from state.schemas.signal import Signal, SignalIntent
from state.schemas.view import (
    CapitalView,
    ConfigsView,
    DashboardView,
    DeltaPCRView,
    HealthView,
    PnLView,
    PositionView,
    StrategyView,
)

__all__ = [
    "CapitalView",
    "ClosedPositionReport",
    "ConfigsView",
    "DashboardView",
    "DayPnL",
    "DeltaPCRCumulative",
    "DeltaPCRHistoryEntry",
    "DeltaPCRInterval",
    "DeltaPCRView",
    "DependencyStatus",
    "EngineStatus",
    "ExecutionConfig",
    "ExitProfile",
    "ExitReason",
    "HealthSummary",
    "HealthView",
    "IndexConfig",
    "IndexMeta",
    "Latencies",
    "MarketSnapshot",
    "OptionChainEntry",
    "OptionContract",
    "OrderEvent",
    "OrderEventEntry",
    "OrderEventType",
    "PerIndexPnL",
    "PnLBreakdown",
    "PnLView",
    "Position",
    "PositionStage",
    "PositionView",
    "RiskConfig",
    "SessionConfig",
    "Signal",
    "SignalIntent",
    "StrategyView",
]
