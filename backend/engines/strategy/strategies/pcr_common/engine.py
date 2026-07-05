"""Shared per-tick engine for the four pcr_analytics strategies.

Each strategy subclasses `PcrStrategyBase` and provides ONLY its signal
state machine (`compute_signal`). Everything else — dynamic ATM±N
subscription, entry gates, and the COMPLETE exit stack — runs here but is
driven entirely by the SUBCLASS's OWN config blob, so every strategy is
self-contained: its own SL/target/TSL/peak-trail/time/EOD settings, its own
cooldowns, its own band. No universal exit config is shared between them.

Exit stack (evaluated on EVERY tick, priorities per the original engine):
    eod force -> peak-trail -> time exit -> counter-crossover -> SL ->
    target -> TSL ratchet (state update).
Exits are emitted as EXIT actions; order-exec's exit_pull path closes the
position instantly. On a counter-crossover exit the new side is queued and
entered on the next tick (the original's same-tick pass-2 reopen).
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
from engines.strategy.strategies.pcr_common.indicators import (
    ChainMap,
    LtpStrengthState,
    OiDiffState,
    VwapState,
)

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(slots=True, frozen=True)
class IndexView:
    """Live per-tick view of one index chain (built from MarketView)."""

    instrument_id: str
    now_ms: int
    spot: float | None
    atm: int
    ce: ChainMap
    pe: ChainMap


@dataclass(slots=True)
class PositionRef:
    """The strategy's own record of its open leg (reference prices)."""

    side: str  # "CE" | "PE"
    strike: int
    token: str
    entry_ref: float
    entry_ts_ms: int
    hwm: float
    sl_price: float | None
    target_price: float | None


@dataclass(slots=True)
class PcrMemory:
    """Session state. Indicator states are per-strategy (only one is used)."""

    oi: OiDiffState = field(default_factory=OiDiffState)
    vol_prev: str | None = None
    vwap: VwapState = field(default_factory=VwapState)
    ltp: LtpStrengthState = field(default_factory=LtpStrengthState)

    pos: PositionRef | None = None
    pending_side: str | None = None  # queued reopen after counter-crossover
    last_entry_ms: int = 0
    entries_today: int = 0
    sub_atm: int = 0
    sub_shift_ms: int = 0

    # VesselMemory protocol
    last_action_kind: ActionKind | None = None
    held_token: str | None = None
    held_strike: int | None = None
    held_side: str | None = None
    suppress_until_ts: int = 0


def _hhmmss(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000.0, tz=_IST).strftime("%H:%M:%S")


class PcrStrategyBase:
    """Strategy-protocol implementation shared by the four PCR strategies."""

    # ── Lifecycle hooks ────────────────────────────────────────────────
    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_config_reload(self, ctx: VesselContext, memory: Any) -> None:
        return

    def create_memory(self, ctx: VesselContext) -> PcrMemory:
        return PcrMemory()

    # ── Subclass hook ──────────────────────────────────────────────────
    def compute_signal(
        self, view: IndexView, memory: PcrMemory, cfg: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        """Return (fresh crossover BUY/SELL or None, metrics)."""
        raise NotImplementedError

    # ── Dynamic ATM±N subscription (never drops the held leg) ─────────
    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None:
        if not isinstance(memory, PcrMemory):
            return None
        spot = market.spot.get("ltp")
        if not isinstance(spot, int | float) or spot <= 0:
            return None
        icfg = ctx.instrument_config or {}
        scfg = ctx.strategy_config or {}
        step = int(icfg.get("strike_step", 50))
        rng = int((scfg.get("universe") or {}).get("subscribe_range", 7))
        hysteresis_ms = int((scfg.get("universe") or {}).get("hysteresis_sec", 5)) * 1000
        atm = int(round(float(spot) / step) * step)
        if atm == memory.sub_atm:
            return None
        if memory.sub_atm and market.now_ms - memory.sub_shift_ms < hysteresis_ms:
            return None
        tokens: list[str] = []
        for i in range(-rng, rng + 1):
            for side in ("CE", "PE"):
                token = market.token_lookup(atm + i * step, side)
                if token:
                    tokens.append(token)
        if not tokens:
            return None
        if memory.pos and memory.pos.token and memory.pos.token not in tokens:
            tokens.append(memory.pos.token)  # keep the held leg live for exits
        memory.sub_atm = atm
        memory.sub_shift_ms = market.now_ms
        return UniverseUpdate(
            subscribe=tuple(tokens),
            basket_view={"atm": atm, "range": rng},
            reason=f"atm_shift_{atm}",
        )

    def build_snapshot(self, ctx: VesselContext, memory: Any, market: MarketView) -> IndexView:
        ce: ChainMap = {}
        pe: ChainMap = {}
        for strike_raw, sides in market.chain.items():
            if not isinstance(sides, dict):
                continue
            try:
                strike = int(strike_raw)
            except (TypeError, ValueError):
                continue
            if isinstance(sides.get("ce"), dict):
                ce[strike] = sides["ce"]
            if isinstance(sides.get("pe"), dict):
                pe[strike] = sides["pe"]
        spot = market.spot.get("ltp")
        spot_f = float(spot) if isinstance(spot, int | float) and spot > 0 else None
        step = int((ctx.instrument_config or {}).get("strike_step", 50))
        atm = int(round((spot_f or 0) / step) * step) if spot_f else 0
        return IndexView(
            instrument_id=ctx.instrument_id,
            now_ms=market.now_ms,
            spot=spot_f,
            atm=atm,
            ce=ce,
            pe=pe,
        )

    # ── Decision function: exits first, then entries ───────────────────
    def on_tick(self, ctx: VesselContext, snapshot: Any, memory: Any) -> Action:
        if not isinstance(snapshot, IndexView) or not isinstance(memory, PcrMemory):
            return Action(ActionKind.NO_OP, reason="bad_input_types")
        cfg = ctx.strategy_config or {}
        icfg = ctx.instrument_config or {}
        now_str = _hhmmss(snapshot.now_ms)
        market_open = str((cfg.get("session") or {}).get("market_open", "09:15:00"))
        market_close = str((cfg.get("session") or {}).get("market_close", "15:30:00"))

        if now_str < market_open:
            return Action(ActionKind.NO_OP, reason="waiting_market_open")
        if snapshot.spot is None or snapshot.atm <= 0:
            return Action(ActionKind.NO_OP, reason="NO_SPOT")

        signal, metrics = self.compute_signal(snapshot, memory, cfg)
        metrics["signal"] = signal

        # ── Exits (own stack, own config; first match wins) ────────────
        if memory.pos is not None:
            action = self._evaluate_exit(snapshot, memory, cfg, signal, now_str, market_close)
            if action is not None:
                action.metrics.update(metrics)
                return action
            return Action(ActionKind.HOLD, side=memory.pos.side, reason="holding", metrics=metrics)

        # ── Entries ─────────────────────────────────────────────────────
        side_signal = signal or memory.pending_side
        memory.pending_side = None
        if side_signal is None:
            return Action(ActionKind.NO_OP, reason="no_crossover", metrics=metrics)

        entry_cfg = cfg.get("entry") or {}
        no_entry_after = str(entry_cfg.get("no_entry_after") or "15:25:00")
        if now_str >= no_entry_after:
            return Action(ActionKind.NO_OP, reason="entry_cutoff", metrics=metrics)
        max_day = int(entry_cfg.get("max_entries_per_day", 0))
        if max_day and memory.entries_today >= max_day:
            return Action(ActionKind.NO_OP, reason="MAX_ENTRIES_PER_DAY", metrics=metrics)
        cooldown_ms = int(entry_cfg.get("cooldown_minutes", 0)) * 60_000
        if (
            cooldown_ms
            and memory.last_entry_ms
            and (snapshot.now_ms - memory.last_entry_ms < cooldown_ms)
        ):
            return Action(ActionKind.NO_OP, reason="cooldown", metrics=metrics)

        side = "CE" if side_signal == "BUY" else "PE"
        step = int(icfg.get("strike_step", 50))
        offset = int((cfg.get("instrument_selection") or {}).get("strike_offset", 0))
        strike = snapshot.atm + (offset * step if side == "CE" else -offset * step)
        leaf = (snapshot.ce if side == "CE" else snapshot.pe).get(strike)
        ltp = float((leaf or {}).get("ltp") or 0)
        if leaf is None or ltp <= 0 or not leaf.get("token"):
            return Action(ActionKind.NO_OP, reason="LEAF_NOT_READY", metrics=metrics)
        max_age_ms = int(float(entry_cfg.get("max_leaf_age_sec", 10)) * 1000)
        if int(leaf.get("ts") or 0) < snapshot.now_ms - max_age_ms:
            return Action(ActionKind.NO_OP, reason="OPTION_TICK_STALE", metrics=metrics)

        exits = cfg.get("exits") or {}
        sl_pct = float(exits.get("sl_pct", 0) or 0)
        target_pct = float(exits.get("target_pct", 0) or 0)
        memory.pos = PositionRef(
            side=side,
            strike=strike,
            token=str(leaf["token"]),
            entry_ref=ltp,
            entry_ts_ms=snapshot.now_ms,
            hwm=ltp,
            sl_price=ltp * (1 - sl_pct / 100.0) if sl_pct > 0 else None,
            target_price=ltp * (1 + target_pct / 100.0) if target_pct > 0 else None,
        )
        memory.last_entry_ms = snapshot.now_ms
        memory.entries_today += 1
        return Action(
            ActionKind.ENTER,
            side=side,
            strike=strike,
            instrument_token=str(leaf["token"]),
            qty_lots=int(icfg.get("qty_lots", 1)),
            reason=f"crossover_{side_signal.lower()}",
            metrics=metrics | {"entry_ref": ltp},
            snapshot={"signal": side_signal, "metrics": metrics, "strike": strike},
        )

    def _evaluate_exit(
        self,
        view: IndexView,
        memory: PcrMemory,
        cfg: dict[str, Any],
        signal: str | None,
        now_str: str,
        market_close: str,
    ) -> Action | None:
        pos = memory.pos
        assert pos is not None
        exits = cfg.get("exits") or {}
        leaf = (view.ce if pos.side == "CE" else view.pe).get(pos.strike) or {}
        ltp = float(leaf.get("ltp") or 0)

        def _exit(reason: str) -> Action:
            side, strike, token = pos.side, pos.strike, pos.token
            memory.pos = None
            return Action(
                ActionKind.EXIT,
                side=side,
                strike=strike,
                instrument_token=token,
                reason=reason,
                metrics={"exit_ref": ltp},
            )

        # eod force (close - 5s buffer approximated by >= close time)
        if now_str >= market_close:
            return _exit("exit_eod")
        if ltp <= 0:
            return None  # data gap — only forced exits fire (original behavior)

        if ltp > pos.hwm:
            pos.hwm = ltp
        # peak-trail: once in profit, exit if premium retraces below pct of peak
        peak_pct = float(exits.get("peak_trail_pct", 0) or 0)
        if exits.get("peak_trail_enabled") and peak_pct > 0 and pos.hwm > pos.entry_ref:
            if ltp <= pos.hwm * (peak_pct / 100.0):
                return _exit("exit_trail")
        # time exit
        time_at = str(exits.get("time_exit_at") or "")
        if exits.get("time_exit_enabled") and time_at and now_str >= time_at:
            return _exit("exit_time")
        # counter-crossover: opposite signal closes the leg; reopen queued
        if exits.get("exit_on_counter_crossover", True) and signal is not None:
            counter = "SELL" if pos.side == "CE" else "BUY"
            if signal == counter:
                memory.pending_side = signal
                return _exit("exit_crossover")
        # stop loss / target
        if pos.sl_price is not None and ltp <= pos.sl_price:
            return _exit("exit_sl")
        if pos.target_price is not None and ltp >= pos.target_price:
            return _exit("exit_target")
        # TSL ratchet (state update only)
        if exits.get("trailing_sl_enabled"):
            trig = float(exits.get("trailing_sl_trigger_pct", 0) or 0)
            step_pct = float(exits.get("trailing_sl_step_pct", 0) or 0)
            if trig > 0 and step_pct > 0 and pos.hwm >= pos.entry_ref * (1 + trig / 100.0):
                new_sl = pos.hwm * (1 - step_pct / 100.0)
                if pos.sl_price is None or new_sl > pos.sl_price:
                    pos.sl_price = new_sl
        return None
