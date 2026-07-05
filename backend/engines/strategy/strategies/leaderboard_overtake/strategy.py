"""LeaderboardOvertakeStrategy — the original rank-momentum overtake pipeline.

Per tick:
    1. rank all valid NIFTY-50 stocks by % change vs prev_close
       (validity filters: live LTP, circuit-limit exclusion, suspended
       exclusion — ported from the original leaderboard)
    2. detect rank-1 OVERTAKES per category (new rank-1 previously visible
       at rank > 1 — original overtake_tracker semantics); queue them
    3. evaluate ONE queued overtake through the full original entry pipeline:
         a. overtake validity (new_rank == 1 — true by construction)
         b. churn filters: pair-flip rejection + pair/symbol cooldowns
         c. change-% threshold (custom, or auto_premarket = the 09:10
            settlement leaderboard's rank-1 %)
         d. per-symbol re-entry cooldown (no pyramiding; platform adaptation
            of the original max_positions_per_symbol=1)
         e. direction (basic / post_settlement_bias / fixed) + optional
            direction-conflict rejection
         f. strike selection (ATM/OTM/ITM + offset) + premium range filter
         g. option freshness (leaf must have ticked within max_leaf_age_sec)
       -> ONE ENTER per tick; remaining overtakes evaluate on later ticks

Pre-open (~09:10) it captures the settlement leaderboard snapshot (rank-1
gainer/loser %) and per-symbol premium snapshots for the bias mode.

The vessel is (leaderboard_overtake_v1, nifty50_stocks) with several
concurrent positions allowed (max_positions_per_vessel). Exits are the
order-exec monitor via the instrument exit profile. The leaderboard top-5
of both sides rides Action.metrics into `metrics:latest` for analytics.
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
from engines.strategy.strategies.open_gainer_loser.strategy import UniverseView

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(slots=True)
class LeaderboardOvertakeMemory:
    """Per-session state (rank history, churn tracker, inventory shadows)."""

    # Rankings from the previous evaluation (overtake detection input).
    prev_gainers: list[dict[str, Any]] = field(default_factory=list)
    prev_losers: list[dict[str, Any]] = field(default_factory=list)

    # Original churn tracker: last event per category, per-pair / per-symbol
    # last-trigger timestamps (epoch ms).
    churn_last_by_category: dict[str, dict[str, Any]] = field(default_factory=dict)
    churn_last_pair_ms: dict[str, int] = field(default_factory=dict)
    churn_last_symbol_ms: dict[str, int] = field(default_factory=dict)

    # Pre-open settlement baselines.
    premarket_leaderboard: dict[str, float] | None = None  # {top_gainer_pct, top_loser_pct}
    premium_snapshots: dict[str, dict[str, dict[int, float]]] | None = None

    bias_history: dict[str, list[str]] = field(default_factory=dict)
    pending_overtakes: list[dict[str, Any]] = field(default_factory=list)
    entered_symbols: dict[str, int] = field(default_factory=dict)  # symbol -> entry ts_ms
    entries_today: int = 0
    subscribed: bool = False

    # VesselMemory protocol
    last_action_kind: ActionKind | None = None
    held_token: str | None = None
    held_strike: int | None = None
    held_side: str | None = None
    suppress_until_ts: int = 0


def _hhmmss(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000.0, tz=_IST).strftime("%H:%M:%S")


def _pair_key(category: str, symbol_a: str, symbol_b: str) -> str:
    left, right = sorted((symbol_a, symbol_b))
    return f"{category}|{left}|{right}"


def _empty_chain(_symbol: str) -> dict[str, Any]:
    return {}


def check_overtake_churn(
    memory: LeaderboardOvertakeMemory,
    overtake: dict[str, Any],
    churn_cfg: dict[str, Any],
) -> tuple[bool, str]:
    """Port of the original `_filter_overtake_churn`: rejects rank-1
    ping-pong (A<->B flips) and fast re-triggers; updates the tracker even on
    rejection so repeated churn stays blocked."""
    if not churn_cfg.get("enabled", False):
        return True, ""

    symbol = str(overtake.get("symbol", ""))
    previous_rank1 = str(overtake.get("previous_rank_1", ""))
    category = str(overtake.get("category", ""))
    now_ms = int(overtake.get("timestamp_ms", 0))
    if not symbol or not previous_rank1 or not category:
        return True, ""

    reject_pair_flip = bool(churn_cfg.get("reject_pair_flip", True))
    pair_cooldown_ms = int(float(churn_cfg.get("pair_cooldown_seconds", 0.0)) * 1000)
    symbol_cooldown_ms = int(float(churn_cfg.get("symbol_cooldown_seconds", 0.0)) * 1000)

    pair_key = _pair_key(category, symbol, previous_rank1)
    symbol_key = f"{category}|{symbol}"
    reject = ""

    if symbol_cooldown_ms > 0:
        prev_ms = memory.churn_last_symbol_ms.get(symbol_key, 0)
        age = now_ms - prev_ms
        if prev_ms > 0 and age < symbol_cooldown_ms:
            reject = f"OVERTAKE_SYMBOL_COOLDOWN:{symbol}:{age / 1000:.2f}s"

    if not reject and pair_cooldown_ms > 0:
        prev_ms = memory.churn_last_pair_ms.get(pair_key, 0)
        age = now_ms - prev_ms
        if prev_ms > 0 and age < pair_cooldown_ms:
            reject = f"OVERTAKE_PAIR_COOLDOWN:{symbol}/{previous_rank1}:{age / 1000:.2f}s"

    if not reject and reject_pair_flip:
        last_evt = memory.churn_last_by_category.get(category)
        if last_evt:
            same_pair = last_evt.get("pair_key", "") == pair_key
            opposite_rotation = (
                last_evt.get("symbol", "") == previous_rank1
                and last_evt.get("previous_rank_1", "") == symbol
            )
            if same_pair and opposite_rotation:
                reject = f"OVERTAKE_PAIR_FLIP:{symbol}<->{previous_rank1}"

    # Update tracker even on rejection (original behavior).
    memory.churn_last_by_category[category] = {
        "timestamp_ms": now_ms,
        "symbol": symbol,
        "previous_rank_1": previous_rank1,
        "pair_key": pair_key,
    }
    memory.churn_last_pair_ms[pair_key] = now_ms
    memory.churn_last_symbol_ms[symbol_key] = now_ms

    return (reject == ""), reject


def check_change_pct_threshold(
    memory: LeaderboardOvertakeMemory,
    entry: dict[str, Any],
    category: str,
    cfg: dict[str, Any],
) -> tuple[bool, str]:
    """Port of the original threshold filter: reject moves smaller than the
    configured (or 09:10 auto-premarket) % change."""
    if not cfg.get("enabled", False):
        return True, ""
    ltp = float(entry.get("ltp") or 0)
    prev_close = float(entry.get("prev_close") or 0)
    if prev_close <= 0 or ltp <= 0:
        return True, ""
    change_pct = abs((ltp - prev_close) / prev_close * 100)

    if str(cfg.get("mode", "custom")) == "auto_premarket":
        lb = memory.premarket_leaderboard or {}
        if category == "GAINER":
            threshold = abs(float(lb.get("top_gainer_pct", 0.5)))
        else:
            threshold = abs(float(lb.get("top_loser_pct", 0.5)))
        if not lb:
            threshold = 0.5  # original safe fallback when no snapshot
    else:
        key = "gainer_min_pct" if category == "GAINER" else "loser_min_pct"
        threshold = float(cfg.get(key, 0.5))

    if change_pct < threshold:
        return False, f"CHANGE_PCT_BELOW_THRESHOLD:{change_pct:.2f}%<{threshold:.2f}%"
    return True, ""


class LeaderboardOvertakeStrategy:
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

    def create_memory(self, ctx: VesselContext) -> LeaderboardOvertakeMemory:
        return LeaderboardOvertakeMemory()

    def update_universe(
        self, ctx: VesselContext, memory: Any, market: MarketView
    ) -> UniverseUpdate | None:
        if not isinstance(memory, LeaderboardOvertakeMemory) or memory.subscribed:
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
            read_chain=market.read_chain or _empty_chain,
        )

    # ── Decision function ──────────────────────────────────────────────

    def on_tick(self, ctx: VesselContext, snapshot: Any, memory: Any) -> Action:
        if not isinstance(snapshot, UniverseView) or not isinstance(
            memory, LeaderboardOvertakeMemory
        ):
            return Action(ActionKind.NO_OP, reason="bad_input_types")

        cfg = ctx.strategy_config or {}
        lb_cfg = cfg.get("leaderboard") or {}
        filters_cfg = cfg.get("entry_filters") or {}
        dir_cfg = cfg.get("direction_prediction") or {}
        sel_cfg = cfg.get("instrument_selection") or {}

        market_open = str((cfg.get("session") or {}).get("market_open", "09:15:00"))
        snapshot_time = str(
            (dir_cfg.get("post_settlement_bias") or {}).get("snapshot_time", "09:10:00")
        )
        no_entry_after = str((cfg.get("session") or {}).get("no_entry_after", "15:00:00"))
        now_str = _hhmmss(snapshot.now_ms)

        # ── Rank (this is also the analytics feed) ───────────────────────
        stocks = ranking_mod.valid_stocks(
            snapshot.spot,
            exclude_circuit_limits=bool(lb_cfg.get("exclude_circuit_limits", True)),
            circuit_limit_threshold_pct=float(lb_cfg.get("circuit_limit_threshold_pct", 20.0)),
            exclude_suspended_stocks=bool(lb_cfg.get("exclude_suspended_stocks", True)),
        )
        min_stocks = int(lb_cfg.get("min_stocks_for_ranking", 30))
        metrics: dict[str, Any] = {
            "valid_stocks": len(stocks),
            "entries_today": memory.entries_today,
            "pending_overtakes": len(memory.pending_overtakes),
        }
        if len(stocks) < min_stocks:
            return Action(ActionKind.NO_OP, reason=f"too_few_stocks:{len(stocks)}", metrics=metrics)

        gainers, losers = ranking_mod.rank(stocks)
        metrics["leaderboard"] = {
            "top_gainers": [
                {"symbol": r["symbol"], "change_pct": r["change_pct"]} for r in gainers[:5]
            ],
            "top_losers": [
                {"symbol": r["symbol"], "change_pct": r["change_pct"]} for r in losers[:5]
            ],
        }

        # ── Pre-open: settlement baselines (~09:10, once) ────────────────
        if now_str < market_open:
            if memory.premarket_leaderboard is None and now_str >= snapshot_time:
                memory.premarket_leaderboard = {
                    "top_gainer_pct": float(gainers[0]["change_pct"]),
                    "top_loser_pct": float(losers[0]["change_pct"]),
                }
                memory.premium_snapshots = ranking_mod.capture_premium_snapshots(
                    sorted(snapshot.symbols), snapshot.read_chain
                )
                return Action(
                    ActionKind.NO_OP,
                    reason="settlement_snapshots_captured",
                    metrics=metrics | {"premarket_leaderboard": dict(memory.premarket_leaderboard)},
                )
            # Track rankings pre-open too, but never treat pre-open moves as
            # overtakes: seed prev rankings without detection.
            memory.prev_gainers, memory.prev_losers = gainers, losers
            return Action(ActionKind.NO_OP, reason="waiting_market_open", metrics=metrics)

        # ── Overtake detection (both categories), queue events ──────────
        for category, prev, curr in (
            ("GAINER", memory.prev_gainers, gainers),
            ("LOSER", memory.prev_losers, losers),
        ):
            event = ranking_mod.detect_overtake(prev, curr, category, snapshot.now_ms)
            if event is not None:
                memory.pending_overtakes.append(event)
        memory.prev_gainers, memory.prev_losers = gainers, losers

        if not memory.pending_overtakes:
            return Action(ActionKind.NO_OP, reason="no_overtake", metrics=metrics)

        # Entry freeze late in the session (positions still monitored/exited).
        if now_str >= no_entry_after:
            memory.pending_overtakes.clear()
            return Action(ActionKind.NO_OP, reason="entry_freeze_window", metrics=metrics)

        # ── Evaluate ONE overtake per tick ───────────────────────────────
        overtake = memory.pending_overtakes.pop(0)
        category = str(overtake["category"])
        symbol = str(overtake["symbol"])
        audit: dict[str, Any] = {"overtake": overtake}
        metrics["overtake_symbol"] = symbol
        metrics["overtake_category"] = category

        max_entries = int((ctx.instrument_config or {}).get("max_entries_per_day", 0))
        if max_entries > 0 and memory.entries_today >= max_entries:
            return Action(
                ActionKind.NO_OP, reason="MAX_ENTRIES_PER_DAY", metrics=metrics, snapshot=audit
            )

        # b. churn filters
        ok, reason = check_overtake_churn(
            memory, overtake, filters_cfg.get("discard_overtakes") or {}
        )
        if not ok:
            return Action(ActionKind.NO_OP, reason=reason, metrics=metrics, snapshot=audit)

        entry = next((r for r in stocks if r["symbol"] == symbol), None)
        if entry is None or float(entry.get("ltp") or 0) <= 0:
            return Action(
                ActionKind.NO_OP, reason=f"NO_LTP:{symbol}", metrics=metrics, snapshot=audit
            )

        # c. change-% threshold
        ok, reason = check_change_pct_threshold(
            memory, entry, category, filters_cfg.get("change_pct_threshold") or {}
        )
        if not ok:
            return Action(ActionKind.NO_OP, reason=reason, metrics=metrics, snapshot=audit)

        # d. per-symbol re-entry cooldown (no pyramiding)
        reentry_cooldown_ms = int(float(filters_cfg.get("reentry_cooldown_sec", 900)) * 1000)
        last_entry_ms = memory.entered_symbols.get(symbol, 0)
        if last_entry_ms and snapshot.now_ms - last_entry_ms < reentry_cooldown_ms:
            age = (snapshot.now_ms - last_entry_ms) / 1000
            return Action(
                ActionKind.NO_OP,
                reason=f"SYMBOL_REENTRY_COOLDOWN:{symbol}:{age:.0f}s",
                metrics=metrics,
                snapshot=audit,
            )

        # e. direction + optional conflict rejection
        ce_chain, pe_chain = ranking_mod.split_platform_chain(snapshot.read_chain(symbol))
        history = memory.bias_history.setdefault(symbol, [])
        option_type, direction, bias_details = direction_mod.predict_direction(
            mode=str(dir_cfg.get("mode", "basic")),
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
        audit["direction"] = direction
        audit["bias_calculation"] = bias_details

        conflict = (category == "GAINER" and direction == "BEARISH") or (
            category == "LOSER" and direction == "BULLISH"
        )
        if dir_cfg.get("reject_on_conflict", False) and conflict:
            return Action(
                ActionKind.NO_OP,
                reason=f"DIRECTION_CONFLICT_{category}_{direction}",
                metrics=metrics,
                snapshot=audit,
            )
        if option_type is None:
            return Action(
                ActionKind.NO_OP,
                reason=f"DIRECTION_NEUTRAL_SKIP:{symbol}",
                metrics=metrics,
                snapshot=audit,
            )

        # f. strike selection + premium range
        chain = ce_chain if option_type == "CE" else pe_chain
        leaf, strike, reason = selection_mod.select_strike(
            chain,
            spot=float(entry["ltp"]),
            moneyness=str(sel_cfg.get("strike_reference", "ITM")),
            offset=int(sel_cfg.get("strike_offset", 0)),
            option_type=option_type,
        )
        if leaf is None:
            return Action(
                ActionKind.NO_OP, reason=f"{reason}:{symbol}", metrics=metrics, snapshot=audit
            )
        ok, reason = selection_mod.premium_filter(leaf, filters_cfg.get("premium") or {})
        if not ok:
            return Action(
                ActionKind.NO_OP, reason=f"{reason}:{symbol}", metrics=metrics, snapshot=audit
            )

        # g. option freshness — the leaf must be live, not a stale snapshot.
        max_leaf_age_ms = int(float((cfg.get("entry") or {}).get("max_leaf_age_sec", 10)) * 1000)
        leaf_ts = int(leaf.get("ts") or 0)
        if leaf_ts <= 0 or snapshot.now_ms - leaf_ts > max_leaf_age_ms:
            return Action(
                ActionKind.NO_OP,
                reason=f"OPTION_TICK_STALE:{symbol}",
                metrics=metrics,
                snapshot=audit,
            )

        memory.entered_symbols[symbol] = snapshot.now_ms
        memory.entries_today += 1
        ltp = float(leaf.get("ltp") or 0.0)
        return Action(
            ActionKind.ENTER,
            side=option_type,
            strike=strike,
            instrument_token=str(leaf.get("token")),
            qty_lots=int((ctx.instrument_config or {}).get("qty_lots", 1)),
            reason=f"overtake_{category.lower()}_{symbol.lower()}",
            metrics=metrics | {"entry_symbol": symbol, "entry_ltp": ltp, "strike": strike},
            snapshot=audit
            | {
                "selection": {
                    "strike_reference": str(sel_cfg.get("strike_reference", "ITM")),
                    "strike_offset": int(sel_cfg.get("strike_offset", 0)),
                    "strike": strike,
                    "ltp": ltp,
                }
            },
        )
