"""OpenGainerLoserStrategy — the original rank-momentum `bootstrap_orders`
pipeline on the NIFTY-50 stock universe.

Session timeline (IST, config-driven):

    ~09:10  capture per-symbol settlement premium snapshots (bias baseline)
    09:15   market open. Within `entry.window_sec` (60 s):
              rank all 50 stocks by % change vs prev_close (websocket cp)
              GAINER = rank-1 gainer -> CE side; LOSER = rank-1 loser -> PE
              per side: fresh-stock-tick gate -> direction (basic /
              post_settlement_bias / fixed; NEUTRAL falls back per policy)
              -> strike selection (ATM/OTM/ITM + offset) -> premium filter
              -> fresh-option-tick gate -> ONE ENTER per side
    09:16+  window closed; nothing more for the day (restart-safe)

The vessel is (open_gainer_loser_v1, nifty50_stocks) — a UNIVERSE vessel:
its MarketView carries the aggregate per-symbol spot map + a lazy per-symbol
chain reader. Up to 2 concurrent positions (max_positions_per_vessel).

Exits are the platform order-exec monitor via the instrument-config exit
profile (original mapping: SL -20%, target ceiling 30%, TSL 10%/3%, max
hold 1200 s, EOD square-off).
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
from engines.strategy.strategies.nifty50_common import direction as direction_mod
from engines.strategy.strategies.nifty50_common import ranking as ranking_mod
from engines.strategy.strategies.nifty50_common import selection as selection_mod
from engines.strategy.strategies.nifty50_common.views import UniverseView, empty_chain

_IST = ZoneInfo("Asia/Kolkata")

_CATEGORIES = ("GAINER", "LOSER")


@dataclass(slots=True)
class OpenGainerLoserMemory:
    """Per-session state."""

    premium_snapshots: dict[str, dict[str, dict[int, float]]] | None = None
    bias_history: dict[str, list[str]] = field(default_factory=dict)
    category_state: dict[str, str] = field(
        default_factory=lambda: {"GAINER": "pending", "LOSER": "pending"}
    )
    window_closed: bool = False
    subscribed: bool = False

    # VesselMemory protocol
    last_action_kind: ActionKind | None = None
    held_token: str | None = None
    held_strike: int | None = None
    held_side: str | None = None
    suppress_until_ts: int = 0


def _hhmmss(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000.0, tz=_IST).strftime("%H:%M:%S")


def _ist_epoch_ms_at(now_ms: int, hhmmss: str) -> int:
    now_ist = datetime.fromtimestamp(now_ms / 1000.0, tz=_IST)
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return int(now_ist.replace(hour=h, minute=m, second=s, microsecond=0).timestamp() * 1000)


class OpenGainerLoserStrategy:
    """Implements the Strategy protocol on the nifty50_stocks universe."""

    # ── Lifecycle hooks ────────────────────────────────────────────────

    def prepare(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_pre_open(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_drain(self, ctx: VesselContext, **kwargs: Any) -> None:
        return

    def on_config_reload(self, ctx: VesselContext, memory: Any) -> None:
        return

    # ── Component hooks ────────────────────────────────────────────────

    def create_memory(self, ctx: VesselContext) -> OpenGainerLoserMemory:
        return OpenGainerLoserMemory()

    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None:
        """Register EVERY universe token with the tick router, once.

        Init already seeds `subscriptions:desired` with the full set (all 50
        stocks + all mapped strikes); this hook wires the vessel's dirty-flag
        routing to the same complete set.
        """
        if not isinstance(memory, OpenGainerLoserMemory) or memory.subscribed:
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
        if not isinstance(snapshot, UniverseView) or not isinstance(memory, OpenGainerLoserMemory):
            return Action(ActionKind.NO_OP, reason="bad_input_types")

        cfg = ctx.strategy_config or {}
        entry_cfg = cfg.get("entry") or {}
        dir_cfg = cfg.get("direction_prediction") or {}
        sel_cfg = cfg.get("instrument_selection") or {}
        filters_cfg = cfg.get("filters") or {}
        lb_cfg = cfg.get("leaderboard") or {}

        market_open = str((cfg.get("session") or {}).get("market_open", "09:15:00"))
        snapshot_time = str(
            (dir_cfg.get("post_settlement_bias") or {}).get("snapshot_time", "09:10:00")
        )
        window_sec = int(entry_cfg.get("window_sec", 60))
        now_str = _hhmmss(snapshot.now_ms)

        pending = [c for c in _CATEGORIES if memory.category_state[c] == "pending"]
        base_metrics: dict[str, Any] = {
            "category_state": dict(memory.category_state),
            "snapshot_symbols": len(memory.premium_snapshots or {}),
        }

        if not pending:
            return Action(ActionKind.HOLD, reason="both_sides_done", metrics=base_metrics)
        if memory.window_closed:
            return Action(ActionKind.NO_OP, reason="bootstrap_window_closed", metrics=base_metrics)

        # ── Pre-open: settlement premium snapshots (~09:10, once) ────────
        if now_str < market_open:
            if memory.premium_snapshots is None and now_str >= snapshot_time:
                snaps = ranking_mod.capture_premium_snapshots(
                    sorted(snapshot.symbols), snapshot.read_chain
                )
                if snaps:
                    memory.premium_snapshots = snaps
                    return Action(
                        ActionKind.NO_OP,
                        reason=f"settlement_snapshots_captured:{len(snaps)}",
                        metrics=base_metrics | {"snapshot_symbols": len(snaps)},
                    )
            return Action(ActionKind.NO_OP, reason="waiting_market_open", metrics=base_metrics)

        # ── Post-open window guard (restart-safe, original 60 s) ─────────
        open_ms = _ist_epoch_ms_at(snapshot.now_ms, market_open)
        seconds_since_open = (snapshot.now_ms - open_ms) / 1000.0
        if seconds_since_open > window_sec:
            memory.window_closed = True
            return Action(
                ActionKind.NO_OP,
                reason=f"bootstrap_window_expired:{seconds_since_open:.0f}s",
                metrics=base_metrics,
            )

        # ── Rank the universe ────────────────────────────────────────────
        stocks = ranking_mod.valid_stocks(
            snapshot.spot,
            exclude_circuit_limits=bool(lb_cfg.get("exclude_circuit_limits", True)),
            circuit_limit_threshold_pct=float(lb_cfg.get("circuit_limit_threshold_pct", 20.0)),
            exclude_suspended_stocks=bool(lb_cfg.get("exclude_suspended_stocks", True)),
        )
        if not stocks:
            return Action(ActionKind.NO_OP, reason="no_valid_stocks", metrics=base_metrics)
        gainers, losers = ranking_mod.rank(stocks)
        leader = {"GAINER": gainers[0], "LOSER": losers[0]}

        metrics = base_metrics | {
            "seconds_since_open": round(seconds_since_open, 1),
            "top_gainer": leader["GAINER"]["symbol"],
            "top_gainer_pct": leader["GAINER"]["change_pct"],
            "top_loser": leader["LOSER"]["symbol"],
            "top_loser_pct": leader["LOSER"]["change_pct"],
        }

        # ── One side per tick; the other side fires on the next tick ─────
        category = pending[0]
        entry = leader[category]
        symbol = str(entry["symbol"])

        # Gate 1 (original): the STOCK must have a live post-open tick so
        # the spot used for strike selection is real, not pre-open.
        if bool(entry_cfg.get("wait_for_fresh_tick", True)) and int(entry["ts"]) < open_ms:
            return Action(
                ActionKind.NO_OP,
                reason=f"waiting_first_live_stock_tick:{symbol}",
                metrics=metrics,
            )

        ce_chain, pe_chain = ranking_mod.split_platform_chain(snapshot.read_chain(symbol))
        history = memory.bias_history.setdefault(symbol, [])
        option_type, direction, bias_details = direction_mod.predict_direction(
            mode=str(dir_cfg.get("mode", "post_settlement_bias")),
            category=category,
            fixed_side=str(dir_cfg.get("fixed_side", "CE")),
            neutral_fallback=str(dir_cfg.get("neutral_fallback", "category")),
            smoothing_enabled=bool(dir_cfg.get("smoothing_enabled", False)),
            smoothing_periods=int(dir_cfg.get("smoothing_periods", 3)),
            bias_history=history,
            snapshot=(memory.premium_snapshots or {}).get(symbol),
            ce_chain=ce_chain,
            pe_chain=pe_chain,
            spot=float(entry["ltp"]),
            bias_cfg=dir_cfg.get("post_settlement_bias") or {},
        )
        history.append(direction)
        max_history = int(dir_cfg.get("smoothing_periods", 3)) * 2
        if len(history) > max_history:
            memory.bias_history[symbol] = history[-max_history:]

        audit: dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "change_pct": entry["change_pct"],
            "direction": direction,
            "bias_calculation": bias_details,
            "leaderboard_top": {
                "gainer": leader["GAINER"]["symbol"],
                "loser": leader["LOSER"]["symbol"],
            },
        }

        if option_type is None:
            return Action(
                ActionKind.NO_OP,
                reason=f"DIRECTION_NEUTRAL_SKIP:{symbol}",
                metrics=metrics,
                snapshot=audit,
            )

        chain = ce_chain if option_type == "CE" else pe_chain
        leaf, strike, reason = selection_mod.select_strike(
            chain,
            spot=float(entry["ltp"]),
            moneyness=str(sel_cfg.get("strike_reference", "ITM")),
            offset=int(sel_cfg.get("strike_offset", 0)),
            option_type=option_type,
        )
        if leaf is None:
            # Chain may still be filling right after open — retry within window.
            return Action(
                ActionKind.NO_OP, reason=f"{reason}:{symbol}", metrics=metrics, snapshot=audit
            )

        ok, reason = selection_mod.premium_filter(leaf, filters_cfg.get("premium") or {})
        if not ok:
            # Terminal for this side (original marks the category handled).
            memory.category_state[category] = "rejected"
            return Action(
                ActionKind.NO_OP, reason=f"{reason}:{symbol}", metrics=metrics, snapshot=audit
            )

        # Gate 2 (original): the SELECTED OPTION must have a live post-open
        # tick — otherwise entry_price would be the stale pre-open premium.
        fresh_gate = bool(entry_cfg.get("wait_for_fresh_tick", True))
        if fresh_gate and int(leaf.get("ts") or 0) < open_ms:
            return Action(
                ActionKind.NO_OP,
                reason=f"waiting_first_live_option_tick:{symbol}",
                metrics=metrics,
                snapshot=audit,
            )

        memory.category_state[category] = "entered"
        ltp = float(leaf.get("ltp") or 0.0)
        return Action(
            ActionKind.ENTER,
            side=option_type,
            strike=strike,
            instrument_token=str(leaf.get("token")),
            qty_lots=int((ctx.instrument_config or {}).get("qty_lots", 1)),
            reason=f"bootstrap_{category.lower()}_{symbol.lower()}",
            metrics=metrics | {"entry_symbol": symbol, "entry_ltp": ltp, "strike": strike},
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
