"""rank_momentum_v2 — Final Foolproof Rank Momentum Specification v2.

Separate BUY-CE and BUY-PE engines over the NIFTY-50 stock-option universe,
driven by executed option order flow (premium notional):

    per symbol, per recompute:  CEΔ/PEΔ since session start -> Net Delta,
    Delta Velocity (30s/1m) + Acceleration, Relative ND, spot-vs-session-mean
    -> Composite Spike / Momentum / Confidence scores
    all symbols ranked by Dynamic Rank Score (bullish book: ND descending;
    bearish book: ND ascending) -> Rank Velocity, Time-at-Rank, Overtakes

Entry (spec 15/16):  BUY CE iff ND > +threshold, DV > 0, RV >= 0, rank in
top-N of the bullish book, CS >= confidence_min (default 0.85). BUY PE is
the mirror on the bearish book. Strike = ATM (offset configurable).

Exit (spec 17):      per tick on every held leg — Exit Score
(0.40 flip-off + 0.30 rank loss + 0.20 DV reversal + 0.10 VWAP loss) over
its threshold closes the leg; plus this strategy's OWN SL / peak-trail /
time / EOD backstops. All exits fire via EXIT signals (order-exec
exit_pull), tick-instant.

Cadence: held-leg exits + flow updates run on EVERY tick; the full 50-stock
ranking table recomputes at `compute_interval_ms` (spec: refresh 1-5 s).
Only signal-relevant metrics persist (metrics:latest) — no fixed logging.
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
from engines.strategy.strategies.nifty50_common.views import UniverseView, empty_chain
from engines.strategy.strategies.rank_momentum import flow as flow_mod

STRATEGY_ID = "rank_momentum_v2"
STRATEGY_NAME = "Rank Momentum"
STRATEGY_DESCRIPTION = (
    "Foolproof Rank Momentum v2: ranks all NIFTY-50 stocks by option order "
    "flow (Net Delta / Delta Velocity / Dynamic Rank Score); buys the CE of "
    "confidence-qualified bullish leaders and the PE of bearish leaders. "
    "Exits on the spec's Exit Score (flip-off, rank loss, DV reversal, "
    "VWAP loss) evaluated tick-by-tick."
)

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(slots=True)
class HeldLeg:
    symbol: str
    side: str
    strike: int
    token: str
    entry_ref: float
    entry_ts_ms: int
    rank_at_entry: int
    hwm: float


@dataclass(slots=True)
class RankMomentumMemory:
    flows: dict[str, flow_mod.SymbolFlow] = field(default_factory=dict)
    bull_ranks: dict[str, flow_mod.RankState] = field(default_factory=dict)
    bear_ranks: dict[str, flow_mod.RankState] = field(default_factory=dict)
    legs: dict[str, HeldLeg] = field(default_factory=dict)  # keyed "SYM:side"
    last_compute_ms: int = 0
    board: list[dict[str, Any]] = field(default_factory=list)
    entries_today: int = 0
    last_entry_ms: dict[str, int] = field(default_factory=dict)
    subscribed: bool = False

    # VesselMemory protocol
    last_action_kind: ActionKind | None = None
    held_token: str | None = None
    held_strike: int | None = None
    held_side: str | None = None
    suppress_until_ts: int = 0


def _hhmmss(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000.0, tz=_IST).strftime("%H:%M:%S")


def _chain_notionals(chain: dict[str, Any]) -> tuple[float, float]:
    """(CE, PE) executed premium notional = Σ vol × ltp over the symbol chain."""
    ce_n = pe_n = 0.0
    for sides in (chain or {}).values():
        if not isinstance(sides, dict):
            continue
        ce, pe = sides.get("ce"), sides.get("pe")
        if isinstance(ce, dict):
            ce_n += float(ce.get("vol") or 0) * float(ce.get("ltp") or 0)
        if isinstance(pe, dict):
            pe_n += float(pe.get("vol") or 0) * float(pe.get("ltp") or 0)
    return ce_n, pe_n


class RankMomentumStrategy:
    """Implements the Strategy protocol on the nifty50_stocks universe."""

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_config_reload(self, ctx: VesselContext, memory: Any) -> None:
        return

    def create_memory(self, ctx: VesselContext) -> RankMomentumMemory:
        return RankMomentumMemory()

    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None:
        if not isinstance(memory, RankMomentumMemory) or memory.subscribed:
            return None
        token_map = market.meta.get("token_map") or {}
        if not token_map:
            return None
        memory.subscribed = True
        return UniverseUpdate(
            subscribe=tuple(token_map.keys()),
            basket_view={"universe": ctx.instrument_id, "tokens": len(token_map)},
            reason="universe_full_subscription",
        )

    def build_snapshot(self, ctx: VesselContext, memory: Any, market: MarketView) -> UniverseView:
        return UniverseView(
            instrument_id=ctx.instrument_id,
            now_ms=market.now_ms,
            spot=market.spot,
            symbols=market.meta.get("symbols") or {},
            read_chain=market.read_chain or empty_chain,
        )

    # ── Decision function ──────────────────────────────────────────────
    def on_tick(self, ctx: VesselContext, snapshot: Any, memory: Any) -> Action:
        if not isinstance(snapshot, UniverseView) or not isinstance(memory, RankMomentumMemory):
            return Action(ActionKind.NO_OP, reason="bad_input_types")
        cfg = ctx.strategy_config or {}
        ind = cfg.get("indicator") or {}
        now_str = _hhmmss(snapshot.now_ms)
        if now_str < str((cfg.get("session") or {}).get("market_open", "09:15:00")):
            return Action(ActionKind.NO_OP, reason="waiting_market_open")

        # ── Held legs: update flows + exits on EVERY tick ───────────────
        exit_action = self._evaluate_exits(snapshot, memory, cfg, now_str)
        if exit_action is not None:
            return exit_action

        # ── Full ranking recompute, throttled (spec: 1-5 s refresh) ────
        interval = int(ind.get("compute_interval_ms", 1000))
        if snapshot.now_ms - memory.last_compute_ms < interval:
            return Action(ActionKind.NO_OP, reason="between_recomputes")
        memory.last_compute_ms = snapshot.now_ms

        rows: list[dict[str, Any]] = []
        for symbol in snapshot.symbols:
            spot_row = snapshot.spot.get(symbol) or {}
            spot = float(spot_row.get("ltp") or 0)
            chain = snapshot.read_chain(symbol)
            if not chain:
                continue
            ce_n, pe_n = _chain_notionals(chain)
            f = memory.flows.setdefault(symbol, flow_mod.SymbolFlow())
            f.update(snapshot.now_ms, ce_n, pe_n, spot)
            rows.append({"symbol": symbol, "flow": f})
        if len(rows) < int(ind.get("min_symbols", 10)):
            return Action(ActionKind.NO_OP, reason=f"too_few_symbols:{len(rows)}")

        # Dual books (separate BUY CE / BUY PE engines): DRS needs ranks, so
        # seed rank order by ND first, then score.
        bull = sorted(rows, key=lambda r: r["flow"].nd, reverse=True)
        bear = sorted(rows, key=lambda r: r["flow"].nd)
        for book, ranks in ((bull, memory.bull_ranks), (bear, memory.bear_ranks)):
            for pos, row in enumerate(book, start=1):
                ranks.setdefault(row["symbol"], flow_mod.RankState()).apply(pos, snapshot.now_ms)

        board: list[dict[str, Any]] = []
        for row in rows:
            symbol = row["symbol"]
            s_bull = flow_mod.scores(row["flow"], memory.bull_ranks[symbol], snapshot.now_ms, ind)
            board.append({"symbol": symbol, **s_bull})
        board.sort(key=lambda r: r["drs"], reverse=True)
        memory.board = board[:10]
        metrics: dict[str, Any] = {
            "leaderboard": [
                {k: r[k] for k in ("symbol", "nd", "dv", "cs", "drs")} for r in board[:5]
            ],
            "open_legs": sorted(memory.legs),
        }

        # ── Entries (spec 15/16) ────────────────────────────────────────
        entry_cfg = cfg.get("entry") or {}
        if now_str >= str(entry_cfg.get("no_entry_after", "15:10:00")):
            return Action(ActionKind.NO_OP, reason="entry_cutoff", metrics=metrics)
        max_day = int(entry_cfg.get("max_entries_per_day", 10))
        if memory.entries_today >= max_day:
            return Action(ActionKind.NO_OP, reason="MAX_ENTRIES_PER_DAY", metrics=metrics)

        nd_threshold = float(ind.get("nd_threshold", 1_000_000))
        cs_min = float(ind.get("confidence_min", 0.85))
        top_n = int(ind.get("rank_top_n", 5))
        cooldown_ms = int(entry_cfg.get("symbol_cooldown_sec", 900)) * 1000

        for side, book, ranks, sign in (
            ("CE", bull, memory.bull_ranks, 1.0),
            ("PE", bear, memory.bear_ranks, -1.0),
        ):
            for row in book[:top_n]:
                symbol = row["symbol"]
                sf: flow_mod.SymbolFlow = row["flow"]
                rank = ranks[symbol]
                if f"{symbol}:{side}" in memory.legs:
                    continue
                last = memory.last_entry_ms.get(f"{symbol}:{side}", 0)
                if last and snapshot.now_ms - last < cooldown_ms:
                    continue
                # Spec conditions: ND beyond threshold, DV directional,
                # rank improving (RV >= 0), spot confirmation, CS gate.
                if sign * sf.nd <= nd_threshold or sign * sf.dv <= 0:
                    continue
                if rank.rv() < 0:
                    continue
                if sf.spot_mean > 0 and sign * (sf.spot - sf.spot_mean) <= 0:
                    continue
                s = flow_mod.scores(sf, rank, snapshot.now_ms, ind)
                if s["cs"] < cs_min:
                    continue
                action = self._enter(ctx, snapshot, memory, symbol, side, rank.rank, s, metrics)
                if action is not None:
                    return action
        return Action(ActionKind.NO_OP, reason="no_qualified_setup", metrics=metrics)

    # ── Internals ──────────────────────────────────────────────────────
    def _enter(
        self,
        ctx: VesselContext,
        view: UniverseView,
        memory: RankMomentumMemory,
        symbol: str,
        side: str,
        rank_now: int,
        s: dict[str, float],
        metrics: dict[str, Any],
    ) -> Action | None:
        cfg = ctx.strategy_config or {}
        chain = view.read_chain(symbol)
        spot = float((view.spot.get(symbol) or {}).get("ltp") or 0)
        strikes = sorted(int(k) for k in chain)
        if not strikes or spot <= 0:
            return None
        atm = min(strikes, key=lambda x: abs(x - spot))
        offset = int((cfg.get("instrument_selection") or {}).get("strike_offset", 0))
        idx = strikes.index(atm)
        target = idx + (offset if side == "CE" else -offset)
        strike = strikes[max(0, min(len(strikes) - 1, target))]
        leaf = (chain.get(str(strike)) or {}).get(side.lower())
        if not isinstance(leaf, dict):
            return None
        ltp = float(leaf.get("ltp") or 0)
        max_age = int(float((cfg.get("entry") or {}).get("max_leaf_age_sec", 10)) * 1000)
        if ltp <= 0 or not leaf.get("token") or int(leaf.get("ts") or 0) < view.now_ms - max_age:
            return None
        key = f"{symbol}:{side}"
        memory.legs[key] = HeldLeg(
            symbol=symbol,
            side=side,
            strike=strike,
            token=str(leaf["token"]),
            entry_ref=ltp,
            entry_ts_ms=view.now_ms,
            rank_at_entry=rank_now,
            hwm=ltp,
        )
        memory.last_entry_ms[key] = view.now_ms
        memory.entries_today += 1
        return Action(
            ActionKind.ENTER,
            side=side,
            strike=strike,
            instrument_token=str(leaf["token"]),
            qty_lots=int((ctx.instrument_config or {}).get("qty_lots", 1)),
            reason=f"rank_momentum_{side.lower()}_{symbol.lower()}",
            metrics=metrics | {"entry_symbol": symbol, "entry_scores": s},
            snapshot={"symbol": symbol, "rank": rank_now, "scores": s},
        )

    def _evaluate_exits(
        self,
        view: UniverseView,
        memory: RankMomentumMemory,
        cfg: dict[str, Any],
        now_str: str,
    ) -> Action | None:
        if not memory.legs:
            return None
        exits = cfg.get("exits") or {}
        ind = cfg.get("indicator") or {}
        market_close = str((cfg.get("session") or {}).get("market_close", "15:30:00"))
        for key in list(memory.legs):
            leg = memory.legs[key]
            chain = view.read_chain(leg.symbol)
            leaf = (chain.get(str(leg.strike)) or {}).get(leg.side.lower()) or {}
            ltp = float(leaf.get("ltp") or 0)

            def _exit(reason: str, leg: HeldLeg = leg, key: str = key) -> Action:
                del memory.legs[key]
                return Action(
                    ActionKind.EXIT,
                    side=leg.side,
                    strike=leg.strike,
                    instrument_token=leg.token,
                    reason=reason,
                )

            if now_str >= market_close:
                return _exit("exit_eod")
            if ltp <= 0:
                continue
            if ltp > leg.hwm:
                leg.hwm = ltp
            sl_pct = float(exits.get("sl_pct", 20.0) or 0)
            if sl_pct > 0 and ltp <= leg.entry_ref * (1 - sl_pct / 100.0):
                return _exit("exit_sl")
            peak_pct = float(exits.get("peak_trail_pct", 0) or 0)
            if peak_pct > 0 and leg.hwm > leg.entry_ref and ltp <= leg.hwm * (peak_pct / 100.0):
                return _exit("exit_trail")
            max_hold = int(exits.get("max_hold_sec", 0) or 0)
            if max_hold and view.now_ms - leg.entry_ts_ms > max_hold * 1000:
                return _exit("exit_time")
            # Spec (17): Exit Score — flows update on every tick for held syms
            spot_row = view.spot.get(leg.symbol) or {}
            f = memory.flows.get(leg.symbol)
            if f is None:
                continue
            ce_n, pe_n = _chain_notionals(chain)
            f.update(view.now_ms, ce_n, pe_n, float(spot_row.get("ltp") or 0))
            ranks = memory.bull_ranks if leg.side == "CE" else memory.bear_ranks
            rank = ranks.get(leg.symbol)
            if rank is None:
                continue
            es, parts = flow_mod.exit_score(
                side=leg.side, flow=f, rank=rank, rank_at_entry=leg.rank_at_entry, cfg=ind
            )
            if es >= float(exits.get("exit_score_threshold", 0.5)):
                action = _exit("exit_score")
                action.metrics.update({"exit_score": es, **parts})
                return action
        return None
