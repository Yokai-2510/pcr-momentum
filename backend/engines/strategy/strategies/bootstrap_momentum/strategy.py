"""BootstrapMomentumStrategy — one-shot market-open entry.

Ported from the original rank-momentum `bootstrap_orders` pipeline. Timeline
per session (all IST, config-driven):

    ~09:10  capture the settlement premium snapshot {side: {strike: ltp}}
            (post_settlement_bias baseline — original 09:10 snapshot)
    09:15   market open. Within `entry.window_sec` (default 60 s):
              1. spot must be live (> 0)
              2. direction: basic / post_settlement_bias / fixed
                 (NEUTRAL resolved by `neutral_fallback` policy)
              3. strike selection: strike_reference (ATM/OTM/ITM) + offset,
                 CE/PE symmetric
              4. premium range filter
              5. freshness gate: the SELECTED option leaf must have a live
                 post-open tick (leaf.ts >= market-open epoch) — prevents
                 ghost entries at stale pre-open premiums
              -> emit ONE ENTER signal; done for the day
    09:16+  window closed — no entries (also true across process restarts,
            matching the original 60-second guard)

Exit management is the platform's order-exec monitor, driven by this
strategy's instrument-config exit profile. Mapping from the original
`exit_conditions` (their defaults -> ours):

    stop_loss.percentage: -20        -> sl_pct: 0.20
    trailing_target ceiling: 30      -> target_pct: 0.30
      (with trailing_stop trail 3% < trailing_target extend 5%, the 3%
       pullback always fires first, so TSL + 30% ceiling is behaviorally
       equivalent to their trailing-target configuration)
    trailing_stop: arm 10, trail 3   -> tsl_arm_pct: 0.10, tsl_trail_pct: 0.03
    time_exit: 1200 s                -> max_hold_sec: 1200
    eod square-off 15:28             -> order-exec EOD square-off

The full bias-calculation audit trail (`bias_details`) travels on
`Action.snapshot` -> Signal.strategy_snapshot -> position record -> report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engines.strategy.strategies.base import (
    Action,
    ActionKind,
    MarketView,
    UniverseUpdate,
    VesselContext,
)
from engines.strategy.strategies.bootstrap_momentum import direction as direction_mod
from engines.strategy.strategies.bootstrap_momentum import selection as selection_mod

_IST = ZoneInfo("Asia/Kolkata")


# ── Strategy-internal snapshot (this strategy's OWN schema) ────────────────


@dataclass(slots=True, frozen=True)
class BootstrapView:
    """Point-in-time view built from the MarketView chain."""

    instrument_id: str
    now_ms: int
    spot: float | None
    ce_chain: dict[int, dict[str, Any]]  # strike -> leaf
    pe_chain: dict[int, dict[str, Any]]


# ── Strategy-internal memory ───────────────────────────────────────────────


@dataclass(slots=True)
class BootstrapMemory:
    """Per-vessel session state."""

    # Bootstrap lifecycle
    premium_snapshot: dict[str, dict[int, float]] | None = None
    snapshot_captured_ms: int = 0
    bias_history: list[str] = field(default_factory=list)
    entered: bool = False
    window_closed: bool = False
    subscribed_atm: int = 0  # 0 = not yet subscribed

    # VesselMemory protocol (runner position sync)
    last_action_kind: ActionKind | None = None
    held_token: str | None = None
    held_strike: int | None = None
    held_side: str | None = None
    suppress_until_ts: int = 0


# ── Helpers ────────────────────────────────────────────────────────────────


def _hhmmss(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000.0, tz=_IST).strftime("%H:%M:%S")


def _ist_epoch_ms_at(now_ms: int, hhmmss: str) -> int:
    """Epoch-ms of today's (IST) wall-clock time `hhmmss`."""
    now_ist = datetime.fromtimestamp(now_ms / 1000.0, tz=_IST)
    h, m, s = (int(x) for x in hhmmss.split(":"))
    at = now_ist.replace(hour=h, minute=m, second=s, microsecond=0)
    return int(at.timestamp() * 1000)


def _chains_from_market(market: MarketView) -> tuple[dict[int, Any], dict[int, Any]]:
    """Split the platform option_chain into CE / PE strike->leaf maps."""
    ce: dict[int, Any] = {}
    pe: dict[int, Any] = {}
    for strike_raw, sides in market.chain.items():
        if not isinstance(sides, dict):
            continue
        try:
            strike = int(strike_raw)
        except (TypeError, ValueError):
            continue
        ce_leaf = sides.get("ce")
        pe_leaf = sides.get("pe")
        if isinstance(ce_leaf, dict):
            ce[strike] = ce_leaf
        if isinstance(pe_leaf, dict):
            pe[strike] = pe_leaf
    return ce, pe


# ── The strategy ───────────────────────────────────────────────────────────


class BootstrapMomentumStrategy:
    """Implements the Strategy protocol. One ENTER per session per vessel."""

    # ── Lifecycle hooks ────────────────────────────────────────────────

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_config_reload(self, ctx: VesselContext, memory: Any) -> None:
        return  # all config is re-read per evaluation; nothing derived in memory

    # ── Component hooks ────────────────────────────────────────────────

    def create_memory(self, ctx: VesselContext) -> BootstrapMemory:
        return BootstrapMemory()

    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None:
        """Subscribe ATM ± subscribe_range strikes on both sides, once.

        Pre-open the window may re-center if spot drifts to a new ATM; after
        entry it is frozen — the held leg's feed must keep flowing so the
        order-exec exit monitor sees live premiums.
        """
        if not isinstance(memory, BootstrapMemory):
            return None
        if memory.entered:
            return None
        spot = market.spot.get("ltp")
        if not isinstance(spot, int | float) or spot <= 0:
            return None

        instrument_cfg = ctx.instrument_config or {}
        strategy_cfg = ctx.strategy_config or {}
        strike_step = int(instrument_cfg.get("strike_step", 50))
        sub_range = int((strategy_cfg.get("universe") or {}).get("subscribe_range", 6))

        atm = int(round(float(spot) / strike_step) * strike_step)
        if atm == memory.subscribed_atm:
            return None

        tokens: list[str] = []
        for i in range(-sub_range, sub_range + 1):
            strike = atm + i * strike_step
            for side in ("CE", "PE"):
                token = market.token_lookup(strike, side)
                if token:
                    tokens.append(token)
        if not tokens:
            return None

        memory.subscribed_atm = atm
        return UniverseUpdate(
            subscribe=tuple(tokens),
            basket_view={"atm": atm, "range": sub_range},
            reason=f"bootstrap_universe_atm_{atm}",
        )

    def build_snapshot(self, ctx: VesselContext, memory: Any, market: MarketView) -> BootstrapView:
        ce, pe = _chains_from_market(market)
        spot = market.spot.get("ltp")
        return BootstrapView(
            instrument_id=ctx.instrument_id,
            now_ms=market.now_ms,
            spot=float(spot) if isinstance(spot, int | float) and spot > 0 else None,
            ce_chain=ce,
            pe_chain=pe,
        )

    # ── Decision function ──────────────────────────────────────────────

    def on_tick(self, ctx: VesselContext, snapshot: Any, memory: Any) -> Action:
        if not isinstance(snapshot, BootstrapView) or not isinstance(memory, BootstrapMemory):
            return Action(ActionKind.NO_OP, reason="bad_input_types")

        strategy_cfg = ctx.strategy_config or {}
        instrument_cfg = ctx.instrument_config or {}
        entry_cfg = strategy_cfg.get("entry") or {}
        dir_cfg = strategy_cfg.get("direction_prediction") or {}
        sel_cfg = strategy_cfg.get("instrument_selection") or {}
        filters_cfg = strategy_cfg.get("filters") or {}

        market_open = str((strategy_cfg.get("session") or {}).get("market_open", "09:15:00"))
        snapshot_time = str(
            (dir_cfg.get("post_settlement_bias") or {}).get("snapshot_time", "09:10:00")
        )
        window_sec = int(entry_cfg.get("window_sec", 60))

        now_str = _hhmmss(snapshot.now_ms)
        base_metrics: dict[str, Any] = {
            "spot": snapshot.spot,
            "snapshot_captured": memory.premium_snapshot is not None,
            "entered": memory.entered,
        }

        # Already done for the session (entered or window expired).
        if memory.entered:
            return Action(
                ActionKind.HOLD,
                side=memory.held_side,
                reason="bootstrap_done",
                metrics=base_metrics,
            )
        if memory.window_closed:
            return Action(ActionKind.NO_OP, reason="bootstrap_window_closed", metrics=base_metrics)

        # ── Pre-open: capture the settlement premium snapshot (~09:10) ──
        if now_str < market_open:
            if memory.premium_snapshot is None and now_str >= snapshot_time:
                snap = self._capture_snapshot(snapshot)
                if snap is not None:
                    memory.premium_snapshot = snap
                    memory.snapshot_captured_ms = snapshot.now_ms
                    return Action(
                        ActionKind.NO_OP,
                        reason="settlement_snapshot_captured",
                        metrics=base_metrics | {"snapshot_captured": True},
                    )
            return Action(ActionKind.NO_OP, reason="waiting_market_open", metrics=base_metrics)

        # ── Post-open: bounded entry window (original 60-second guard) ──
        open_ms = _ist_epoch_ms_at(snapshot.now_ms, market_open)
        seconds_since_open = (snapshot.now_ms - open_ms) / 1000.0
        if seconds_since_open > window_sec:
            memory.window_closed = True
            return Action(
                ActionKind.NO_OP,
                reason=f"bootstrap_window_expired:{seconds_since_open:.0f}s",
                metrics=base_metrics,
            )

        if snapshot.spot is None:
            return Action(ActionKind.NO_OP, reason="NO_LTP", metrics=base_metrics)

        # ── Direction ────────────────────────────────────────────────
        category = str(instrument_cfg.get("category", "GAINER"))
        option_type, direction, bias_details = direction_mod.predict_direction(
            mode=str(dir_cfg.get("mode", "post_settlement_bias")),
            category=category,
            fixed_side=str(dir_cfg.get("fixed_side", "CE")),
            neutral_fallback=str(dir_cfg.get("neutral_fallback", "category")),
            smoothing_enabled=bool(dir_cfg.get("smoothing_enabled", False)),
            smoothing_periods=int(dir_cfg.get("smoothing_periods", 3)),
            bias_history=memory.bias_history,
            snapshot=memory.premium_snapshot,
            ce_chain=snapshot.ce_chain,
            pe_chain=snapshot.pe_chain,
            spot=snapshot.spot,
            bias_cfg=dir_cfg.get("post_settlement_bias") or {},
        )
        memory.bias_history.append(direction)
        max_history = int(dir_cfg.get("smoothing_periods", 3)) * 2
        if len(memory.bias_history) > max_history:
            memory.bias_history = memory.bias_history[-max_history:]

        audit = {"direction": direction, "category": category, "bias_calculation": bias_details}
        metrics = base_metrics | {
            "direction": direction,
            "seconds_since_open": round(seconds_since_open, 1),
        }

        if option_type is None:
            # NEUTRAL with fallback="skip" — retry next tick within the window.
            return Action(
                ActionKind.NO_OP, reason="DIRECTION_NEUTRAL_SKIP", metrics=metrics, snapshot=audit
            )

        # ── Strike selection ─────────────────────────────────────────
        chain = snapshot.ce_chain if option_type == "CE" else snapshot.pe_chain
        leaf, strike, reason = selection_mod.select_strike(
            chain,
            spot=snapshot.spot,
            moneyness=str(sel_cfg.get("strike_reference", "ITM")),
            offset=int(sel_cfg.get("strike_offset", 0)),
            option_type=option_type,
        )
        if leaf is None:
            return Action(ActionKind.NO_OP, reason=reason, metrics=metrics, snapshot=audit)

        # ── Option-level filters (premium range) ─────────────────────
        ok, reason = selection_mod.premium_filter(leaf, filters_cfg.get("premium") or {})
        if not ok:
            # Filter rejection is terminal for the session (original marked
            # the category handled on rejection too).
            memory.window_closed = True
            return Action(ActionKind.NO_OP, reason=reason, metrics=metrics, snapshot=audit)

        # ── Freshness gate: option must have a live post-open tick ───
        if bool(entry_cfg.get("wait_for_fresh_tick", True)):
            leaf_ts = int(leaf.get("ts") or 0)
            if leaf_ts < open_ms:
                return Action(
                    ActionKind.NO_OP,
                    reason="waiting_first_live_option_tick",
                    metrics=metrics | {"leaf_ts": leaf_ts, "open_ms": open_ms},
                    snapshot=audit,
                )

        # ── Fire ─────────────────────────────────────────────────────
        memory.entered = True
        ltp = float(leaf.get("ltp") or 0.0)
        return Action(
            ActionKind.ENTER,
            side=option_type,
            strike=strike,
            instrument_token=str(leaf.get("token")),
            qty_lots=int(instrument_cfg.get("qty_lots", 1)),
            reason=f"bootstrap_{direction.lower()}",
            metrics=metrics | {"entry_ltp": ltp, "strike": strike},
            snapshot=audit
            | {
                "selection": {
                    "strike_reference": str(sel_cfg.get("strike_reference", "ITM")),
                    "strike_offset": int(sel_cfg.get("strike_offset", 0)),
                    "strike": strike,
                    "ltp": ltp,
                    "seconds_since_open": round(seconds_since_open, 1),
                }
            },
        )

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _capture_snapshot(view: BootstrapView) -> dict[str, dict[int, float]] | None:
        """Record {side: {strike: ltp}} for all strikes with a live premium."""
        ce = {s: float(leaf.get("ltp") or 0.0) for s, leaf in view.ce_chain.items()}
        pe = {s: float(leaf.get("ltp") or 0.0) for s, leaf in view.pe_chain.items()}
        ce = {s: v for s, v in ce.items() if v > 0}
        pe = {s: v for s, v in pe.items() if v > 0}
        if not ce or not pe:
            return None  # no live pre-open premiums yet — retry next tick
        return {"CE": ce, "PE": pe}
