"""
BidAskImbalanceStrategy — implements the Strategy Protocol (base.py).

This is the pure-function orchestrator. It takes a `Snapshot` + `MemoryStore`,
runs the 8 metrics, applies the decision logic, and returns an Action.
ALL Redis I/O happens in the runner (engines.strategy.runner); this class
never touches Redis.

Memory contract:
    memory.buffers           BufferStore         per-strike rolling buffers
    memory.basket            Basket              current basket (read-only here)
    memory.last_action_kind  ActionKind | None   for transition tracking
    memory.timing_windows    list[TimingWindow]  parsed from config

The runner is responsible for:
  - reading Redis -> building Snapshot
  - calling on_tick(ctx, snapshot, memory)
  - applying the returned Action (state transitions, signal emission)
  - persisting metrics back to Redis (last_decision telemetry)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from engines.strategy.strategies.base import Action, ActionKind, Strategy, VesselContext
from engines.strategy.strategies.bid_ask_imbalance.basket import Basket
from engines.strategy.strategies.bid_ask_imbalance.buffer import BufferStore
from engines.strategy.strategies.bid_ask_imbalance.decisions import (
    continuation as continuation_mod,
)
from engines.strategy.strategies.bid_ask_imbalance.decisions import (
    entry_gates as gates_mod,
)
from engines.strategy.strategies.bid_ask_imbalance.decisions import (
    reversal as reversal_mod,
)
from engines.strategy.strategies.bid_ask_imbalance.decisions import timing as timing_mod
from engines.strategy.strategies.bid_ask_imbalance.metrics import imbalance as imbalance_mod
from engines.strategy.strategies.bid_ask_imbalance.metrics.ask_wall import (
    cache_observation_imbalance,
    classify_wall_state,
)
from engines.strategy.strategies.bid_ask_imbalance.metrics.aggressor import detect_aggressor
from engines.strategy.strategies.bid_ask_imbalance.metrics.cumulative import cumulative_imbalance
from engines.strategy.strategies.bid_ask_imbalance.metrics.pressure import (
    classify_pressure,
    net_pressure,
)
from engines.strategy.strategies.bid_ask_imbalance.metrics.quality_score import (
    compute_quality_score,
)
from engines.strategy.strategies.bid_ask_imbalance.metrics.spread import (
    classify_spread,
    compute_spread,
)
from engines.strategy.strategies.bid_ask_imbalance.snapshot import Snapshot, StrikeLeg


@dataclass(slots=True)
class MemoryStore:
    """Per-vessel mutable state. Managed by the runner."""

    buffers: BufferStore
    basket: Basket
    timing_windows: list[timing_mod.TimingWindow] = field(default_factory=list)
    last_action_kind: ActionKind | None = None
    held_token: str | None = None        # set when state moves to IN_CE/IN_PE
    held_strike: int | None = None
    held_side: str | None = None          # "CE" | "PE"
    suppress_until_ts: int = 0            # post-reversal suppression window


@dataclass(slots=True)
class _Thresholds:
    imbalance_strong_buy: float
    imbalance_neutral_low: float
    imbalance_continuation: float
    net_pressure_entry: float
    net_pressure_neutral_band: float
    imbalance_drop_pct: float
    ask_wall_qty_multiple: float
    aggressor_tolerance_inr: float
    tick_min_consecutive: int
    tick_window_ms: int
    spread_good_inr: float
    spread_moderate_inr: float
    reversal_lookback_ticks: int
    reversal_suppress_sec: int


def _read_thresholds(ctx: VesselContext) -> _Thresholds:
    """Pull all thresholds from the strategy + instrument config blobs."""
    s = ctx.strategy_config or {}
    i = ctx.instrument_config or {}
    th = s.get("thresholds", {}) or {}
    ts = s.get("tick_speed", {}) or {}
    rev = s.get("reversal", {}) or {}
    return _Thresholds(
        imbalance_strong_buy=float(th.get("imbalance_strong_buy", 1.30)),
        imbalance_neutral_low=float(th.get("imbalance_neutral_low", 0.90)),
        imbalance_continuation=float(th.get("imbalance_continuation", 1.20)),
        net_pressure_entry=float(th.get("net_pressure_entry_threshold", 0.50)),
        net_pressure_neutral_band=float(th.get("net_pressure_neutral_band", 0.20)),
        imbalance_drop_pct=float(th.get("imbalance_drop_pct_for_reversal", 30.0)),
        ask_wall_qty_multiple=float(th.get("ask_wall_qty_multiple", 5.0)),
        aggressor_tolerance_inr=float(th.get("ltp_aggressor_tolerance_inr", 0.10)),
        tick_min_consecutive=int(ts.get("min_consecutive", 3)),
        tick_window_ms=int(ts.get("window_ms", 1000)),
        spread_good_inr=float(i.get("spread_good_inr", 1.00)),
        spread_moderate_inr=float(i.get("spread_moderate_inr", 2.00)),
        reversal_lookback_ticks=int(rev.get("lookback_ticks", 3)),
        reversal_suppress_sec=int(rev.get("suppress_sec", 30)),
    )


def _per_strike_metric_dict(
    leg: StrikeLeg, thresholds: _Thresholds, wall_state: str, aggressor: str
) -> dict[str, Any]:
    imb = imbalance_mod.compute_imbalance(leg)
    spread = compute_spread(leg)
    return {
        "token": leg.token,
        "strike": leg.strike,
        "side": leg.side,
        "imbalance": round(imb, 4) if imb is not None else None,
        "imbalance_class": imbalance_mod.classify_imbalance(
            imb, strong_buy=thresholds.imbalance_strong_buy
        ),
        "spread": round(spread, 4) if spread is not None else None,
        "spread_class": classify_spread(
            spread,
            good_threshold=thresholds.spread_good_inr,
            moderate_threshold=thresholds.spread_moderate_inr,
        ),
        "wall_state": wall_state,
        "aggressor": aggressor,
        "ltp": leg.ltp,
        "best_bid": leg.best_bid,
        "best_ask": leg.best_ask,
        "best_bid_qty": leg.best_bid_qty,
        "best_ask_qty": leg.best_ask_qty,
        "total_bid_qty": leg.total_bid_qty,
        "total_ask_qty": leg.total_ask_qty,
        "ts": leg.ts,
    }


def _pick_dominant_strike(legs: tuple[StrikeLeg, ...], thresholds: _Thresholds) -> StrikeLeg | None:
    """Return the leg with the highest imbalance (None if no imbalance computable)."""
    best: tuple[float, StrikeLeg] | None = None
    for leg in legs:
        imb = imbalance_mod.compute_imbalance(leg)
        if imb is None:
            continue
        if best is None or imb > best[0]:
            best = (imb, leg)
    return best[1] if best else None


class BidAskImbalanceStrategy:
    """The new primary strategy. Implements the Strategy Protocol."""

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None:
        """No-op; runner builds basket + buffers + timing_windows before the
        first on_tick call."""
        return

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None:
        """No baseline snapshot needed for this strategy (we read live book,
        not pre-open premium). Kept for protocol compliance."""
        return

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None:
        """Drain phase — runner is closing positions. Strategy stops emitting
        new entries; this is a no-op since the runner caps actions in DRAIN."""
        return

    def on_tick(
        self, ctx: VesselContext, snapshot: Any, memory: Any
    ) -> Action:
        if not isinstance(snapshot, Snapshot) or not isinstance(memory, MemoryStore):
            return Action(ActionKind.NO_OP, reason="bad_input_types")

        thresholds = _read_thresholds(ctx)
        ts_ms = snapshot.snapshot_ts or int(time.time() * 1000)

        # ── Phase 1: per-strike metrics + buffer push ─────────────────────
        per_strike: dict[str, dict[str, Any]] = {}
        for leg in snapshot.all_legs:
            buf = memory.buffers.buffer_for(leg.token)
            wall_state = classify_wall_state(
                leg,
                buf,
                qty_multiple=thresholds.ask_wall_qty_multiple,
                aggressor_tolerance_inr=thresholds.aggressor_tolerance_inr,
            )
            aggressor = detect_aggressor(
                leg, tolerance_inr=thresholds.aggressor_tolerance_inr
            )
            imb = imbalance_mod.compute_imbalance(leg)
            spread = compute_spread(leg)
            wall_present = (wall_state in ("HOLDING", "ABSORBING", "REFRESHING"))
            obs = cache_observation_imbalance(
                leg, imb, spread, wall_present if wall_state != "UNKNOWN" else None,
                aggressor, ts_ms,
            )
            buf.push(obs)
            per_strike[leg.token] = _per_strike_metric_dict(
                leg, thresholds, wall_state, aggressor
            )

        # ── Phase 2: cumulative + net pressure ────────────────────────────
        ce_sum_bid, ce_sum_ask, cum_ce = cumulative_imbalance(snapshot.ce_legs)
        pe_sum_bid, pe_sum_ask, cum_pe = cumulative_imbalance(snapshot.pe_legs)
        np_value = net_pressure(cum_ce, cum_pe)
        np_label = classify_pressure(
            np_value,
            entry_threshold=thresholds.net_pressure_entry,
            neutral_band=thresholds.net_pressure_neutral_band,
        )

        base_metrics: dict[str, Any] = {
            "snapshot_ts": ts_ms,
            "atm": snapshot.atm,
            "spot": snapshot.spot,
            "cum_ce_imbalance": round(cum_ce, 4) if cum_ce is not None else None,
            "cum_pe_imbalance": round(cum_pe, 4) if cum_pe is not None else None,
            "ce_sum_bid": ce_sum_bid,
            "ce_sum_ask": ce_sum_ask,
            "pe_sum_bid": pe_sum_bid,
            "pe_sum_ask": pe_sum_ask,
            "net_pressure": round(np_value, 4) if np_value is not None else None,
            "net_pressure_label": np_label,
            "per_strike": per_strike,
        }

        # ── Phase 3: held-position branches (continuation + reversal) ─────
        if memory.held_side and memory.held_token:
            held_leg = next(
                (leg for leg in snapshot.all_legs if leg.token == memory.held_token), None
            )
            if held_leg is None:
                return Action(
                    ActionKind.NO_OP,
                    reason="held_token_not_in_basket",
                    metrics=base_metrics,
                )

            held_buf = memory.buffers.buffer_for(memory.held_token)

            # Reversal first — has higher priority than continuation.
            rev = reversal_mod.evaluate_reversal(
                held_side=memory.held_side,
                leg=held_leg,
                buffer=held_buf,
                imbalance_drop_threshold_pct=thresholds.imbalance_drop_pct,
                spread_good_inr=thresholds.spread_good_inr,
                spread_moderate_inr=thresholds.spread_moderate_inr,
                qty_multiple=thresholds.ask_wall_qty_multiple,
                aggressor_tolerance_inr=thresholds.aggressor_tolerance_inr,
                lookback_ticks=thresholds.reversal_lookback_ticks,
            )
            if rev.triggered:
                # Find dominant strike on the FLIPPED side for re-entry.
                opposite_legs = (
                    snapshot.pe_legs if memory.held_side == "CE" else snapshot.ce_legs
                )
                flip_target = _pick_dominant_strike(opposite_legs, thresholds)
                if flip_target is None:
                    return Action(
                        ActionKind.EXIT,
                        side=memory.held_side,
                        strike=memory.held_strike,
                        instrument_token=memory.held_token,
                        qty_lots=int(ctx.instrument_config.get("qty_lots", 1)),
                        reason="reversal_triggered_no_flip_target",
                        metrics={**base_metrics, "reversal_triggers": rev.triggers},
                    )
                return Action(
                    ActionKind.FLIP,
                    side="PE" if memory.held_side == "CE" else "CE",
                    strike=flip_target.strike,
                    instrument_token=flip_target.token,
                    qty_lots=int(ctx.instrument_config.get("qty_lots", 1)),
                    reason="reversal_triggered",
                    metrics={**base_metrics, "reversal_triggers": rev.triggers},
                )

            # Continuation
            cont = continuation_mod.evaluate_continuation(
                side=memory.held_side,
                held_leg=held_leg,
                buffer=held_buf,
                imbalance_continuation=thresholds.imbalance_continuation,
                spread_good_inr=thresholds.spread_good_inr,
                spread_moderate_inr=thresholds.spread_moderate_inr,
                qty_multiple=thresholds.ask_wall_qty_multiple,
                aggressor_tolerance_inr=thresholds.aggressor_tolerance_inr,
            )
            if not cont.hold:
                return Action(
                    ActionKind.EXIT,
                    side=memory.held_side,
                    strike=memory.held_strike,
                    instrument_token=memory.held_token,
                    qty_lots=int(ctx.instrument_config.get("qty_lots", 1)),
                    reason=f"continuation_failed:{','.join(cont.failures[:3])}",
                    metrics={**base_metrics, "continuation_failures": cont.failures},
                )
            return Action(
                ActionKind.HOLD,
                side=memory.held_side,
                reason="continuation_ok",
                metrics=base_metrics,
            )

        # ── Phase 4: FLAT — evaluate entry gates ──────────────────────────
        if ts_ms < memory.suppress_until_ts:
            return Action(
                ActionKind.NO_OP,
                reason=f"reversal_suppression_until_{memory.suppress_until_ts}",
                metrics=base_metrics,
            )

        # Gate 1: direction
        g1 = gates_mod.gate1_direction(np_value, entry_threshold=thresholds.net_pressure_entry)
        if not g1.passed:
            return Action(ActionKind.NO_OP, reason=g1.reason, metrics=base_metrics)

        chosen_side = g1.side  # "CE" | "PE"
        side_legs = snapshot.ce_legs if chosen_side == "CE" else snapshot.pe_legs
        dominant = _pick_dominant_strike(side_legs, thresholds)
        if dominant is None:
            return Action(
                ActionKind.NO_OP, reason="no_dominant_strike", metrics=base_metrics
            )
        dom_buf = memory.buffers.buffer_for(dominant.token)

        # Gate 2: ask wall
        wall_state = classify_wall_state(
            dominant,
            dom_buf,
            qty_multiple=thresholds.ask_wall_qty_multiple,
            aggressor_tolerance_inr=thresholds.aggressor_tolerance_inr,
        )
        g2 = gates_mod.gate2_ask_wall(wall_state)
        if not g2.passed:
            return Action(ActionKind.NO_OP, reason=g2.reason, metrics=base_metrics)

        # Gate 3: spread
        spread_status = classify_spread(
            compute_spread(dominant),
            good_threshold=thresholds.spread_good_inr,
            moderate_threshold=thresholds.spread_moderate_inr,
        )
        g3 = gates_mod.gate3_spread(spread_status)
        if not g3.passed:
            return Action(ActionKind.NO_OP, reason=g3.reason, metrics=base_metrics)

        # Gate 4: quality score
        qresult = compute_quality_score(
            side=chosen_side,
            dominant_leg=dominant,
            buffer=dom_buf,
            spread_good_inr=thresholds.spread_good_inr,
            spread_moderate_inr=thresholds.spread_moderate_inr,
            imbalance_strong_buy=thresholds.imbalance_strong_buy,
            qty_multiple=thresholds.ask_wall_qty_multiple,
            tick_min_consecutive=thresholds.tick_min_consecutive,
            tick_window_ms=thresholds.tick_window_ms,
            aggressor_tolerance_inr=thresholds.aggressor_tolerance_inr,
        )

        # Time-of-day windowing
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).time()
        allowed, why = timing_mod.entry_allowed(now_ist, memory.timing_windows, qresult.score)
        if not allowed:
            return Action(
                ActionKind.NO_OP,
                reason=why,
                score=qresult.score,
                metrics={**base_metrics, "score": qresult.score, "score_breakdown": qresult.breakdown},
                score_breakdown=qresult.breakdown,
            )

        # All gates passed -> emit ENTRY
        base_qty = int(ctx.instrument_config.get("qty_lots", 1))
        # Combine score-based and spread-based size factors
        size_factor = min(qresult.entry_size_factor, g3.size_factor)
        if size_factor <= 0:
            return Action(
                ActionKind.NO_OP,
                reason=f"size_factor_zero_score_{qresult.score}",
                score=qresult.score,
                metrics={**base_metrics, "score": qresult.score},
                score_breakdown=qresult.breakdown,
            )
        qty_lots = max(1, int(round(base_qty * size_factor)))

        return Action(
            kind=ActionKind.ENTER,
            side=chosen_side,
            strike=dominant.strike,
            instrument_token=dominant.token,
            qty_lots=qty_lots,
            score=qresult.score,
            reason="all_gates_passed",
            metrics={**base_metrics, "score": qresult.score, "score_breakdown": qresult.breakdown},
            score_breakdown=qresult.breakdown,
        )
