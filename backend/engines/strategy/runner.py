"""
Vessel runner — one async task per (strategy_id, instrument_id) pair.

Owns ALL Redis I/O:
  - reads option_chain + spot + meta -> builds a MarketView
  - asks the strategy for universe updates (basket shifts / subscriptions)
  - asks the strategy to build ITS OWN snapshot type from the MarketView
  - calls strategy.on_tick(ctx, snapshot, memory) -> Action
  - writes per-tick decision telemetry (Strategy.md §11.1)
  - applies state transitions
  - calls publisher.emit_signal(...) on actionable Actions

The runner is GENERIC — it imports no concrete strategy code. Everything
strategy-specific (snapshot schema, memory layout, metric names, basket
policy) lives behind the Strategy protocol hooks:

    create_memory / update_universe / build_snapshot / on_tick / on_config_reload

Loop shape (Strategy.md §2.3 — event-driven, no artificial floor):

    while True:
        await dirty.wait()         # blocks at OS level when idle
        dirty.clear()              # reset BEFORE reading state
        market = read_market_view()
        apply(strategy.update_universe(ctx, memory, market))
        snapshot = strategy.build_snapshot(ctx, memory, market)
        action = strategy.on_tick(ctx, snapshot, memory)
        await persist_metrics(action)
        await apply_action(action)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
import redis.asyncio as _redis_async
from loguru import logger

from engines.strategy import publisher
from engines.strategy.ingestion import TickRouter
from engines.strategy.observability import decision_log
from engines.strategy.registry import VesselSpec, reload_vessel_config
from engines.strategy.strategies.base import Action, ActionKind, MarketView, VesselMemory
from engines.strategy.vessel_state import (
    is_enabled,
    maybe_exit_cooldown,
    read_state,
    set_state,
)
from state import keys as K

_IST = ZoneInfo("Asia/Kolkata")


def _decode(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode()
    return str(v)


def _read_json(redis_sync: Any, key: str) -> Any:
    raw = redis_sync.get(key)
    if not raw:
        return None
    try:
        return orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return None


def _read_spot_hash(redis_sync: Any, index: str) -> dict[str, Any]:
    raw = redis_sync.hgetall(K.market_data_index_spot(index))
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        kk = _decode(k)
        vv = _decode(v)
        try:
            out[kk] = (
                float(vv)
                if "." in vv or kk in {"ltp", "prev_close", "change_inr", "change_pct"}
                else int(vv)
            )
        except ValueError:
            out[kk] = vv
    return out


def _read_meta(redis_sync: Any, index: str) -> dict[str, Any]:
    parsed = _read_json(redis_sync, K.market_data_index_meta(index))
    return parsed if isinstance(parsed, dict) else {}


def _build_token_lookup(
    meta: dict[str, Any], chain: dict[str, Any]
) -> Callable[[int, str], str | None]:
    """Build a (strike, side) -> token resolver from meta + current chain.

    The chain (option_chain) already maps strike -> {ce: {token,...}, pe: {token,...}}.
    Falls back to None when a strike isn't in the chain (which happens before
    data-pipeline subscribes that strike — the runner will retry next tick).
    """

    def _lookup(strike: int, side: str) -> str | None:
        sides = chain.get(str(strike))
        if not isinstance(sides, dict):
            return None
        leaf = sides.get(side.lower())
        if not isinstance(leaf, dict):
            return None
        token = leaf.get("token")
        return token if isinstance(token, str) else None

    return _lookup


def _read_market_view(redis_sync: Any, index: str) -> MarketView:
    """One consistent read of everything the strategy hooks may need."""
    chain = _read_json(redis_sync, K.market_data_index_option_chain(index)) or {}
    spot = _read_spot_hash(redis_sync, index)
    meta = _read_meta(redis_sync, index)
    return MarketView(
        chain=chain,
        spot=spot,
        meta=meta,
        now_ms=int(time.time() * 1000),
        token_lookup=_build_token_lookup(meta, chain),
    )


async def _persist_metrics_and_decision(
    redis_async: _redis_async.Redis,
    *,
    sid: str,
    instrument_id: str,
    action: Action,
) -> None:
    """Write the last_decision telemetry block + strategy metrics.

    Always called (Strategy.md §5.1: every tick produces a logged decision,
    even NO_OP). This is what makes silent-loop bugs detectable.

    Metric names are strategy-defined. The full metrics blob is written to
    the vessel's `metrics:latest` key; a few well-known names additionally
    land on their dedicated keys so existing dashboards keep working.
    """
    metrics = action.metrics or {}
    ts_ms = int(time.time() * 1000)

    last_decision = {
        "action": action.kind.value,
        "side": action.side,
        "strike": action.strike,
        "score": action.score,
        "score_breakdown": action.score_breakdown,
        "reason": action.reason,
        "ts_ms": ts_ms,
    }

    pipe = redis_async.pipeline(transaction=False)
    pipe.set(K.vessel_metrics_last_decision(sid, instrument_id), orjson.dumps(last_decision))
    pipe.set(K.vessel_metrics_last_decision_ts(sid, instrument_id), str(ts_ms))

    if metrics:
        pipe.set(
            K.vessel_metrics_latest(sid, instrument_id),
            orjson.dumps(metrics, default=str),
        )

    # Well-known metric names -> dedicated keys (UI views read these).
    if metrics.get("net_pressure") is not None:
        pipe.set(K.vessel_metrics_net_pressure(sid, instrument_id), str(metrics["net_pressure"]))
    if metrics.get("cum_ce_imbalance") is not None:
        pipe.set(K.vessel_metrics_cum_ce(sid, instrument_id), str(metrics["cum_ce_imbalance"]))
    if metrics.get("cum_pe_imbalance") is not None:
        pipe.set(K.vessel_metrics_cum_pe(sid, instrument_id), str(metrics["cum_pe_imbalance"]))
    if metrics.get("per_strike"):
        pipe.set(
            K.vessel_metrics_per_strike(sid, instrument_id),
            orjson.dumps(metrics["per_strike"]),
        )

    await pipe.execute()


async def _apply_action(
    redis_async: _redis_async.Redis,
    redis_sync: Any,
    *,
    spec: VesselSpec,
    action: Action,
    memory: VesselMemory,
) -> None:
    """Translate Action into state mutations + signal emission."""
    sid = spec.strategy_id
    idx = spec.instrument_id
    suppress_sec = int(
        (spec.context.strategy_config.get("reversal", {}) or {}).get("suppress_sec", 30)
    )

    # IMPORTANT: the runner DOES NOT write `state` for entries/flips.
    # Order-execution is the authoritative writer. Order-exec sets state to
    # IN_CE/IN_PE only after a position is confirmed open. Until then the
    # vessel stays FLAT and the strategy may re-emit the same signal on the
    # next tick — sig_id is a deterministic hash so duplicates are collapsed
    # by the allocator's per-vessel cap. This way Redis state never lies
    # about whether a position is actually open.
    if (
        action.kind == ActionKind.ENTER
        or action.kind == ActionKind.FLIP
        or action.kind == ActionKind.EXIT
    ):
        await publisher.emit_signal(
            redis_async,
            strategy_id=sid,
            instrument_id=idx,
            action=action,
        )
        # Order-exec writes state back to FLAT/COOLDOWN on confirmed exit fill.
        # Strategy's exit signal here is just a request.

    elif action.kind == ActionKind.REVERSAL_WARN:
        # Telemetry-only; no signal. Set suppression window.
        memory.suppress_until_ts = int(time.time() * 1000) + suppress_sec * 1000

    # NO_OP / HOLD: nothing to do beyond the metric persistence already done.
    memory.last_action_kind = action.kind


async def vessel_loop(
    *,
    spec: VesselSpec,
    redis_async: _redis_async.Redis,
    redis_sync: Any,
    router: TickRouter,
    shutdown: asyncio.Event,
) -> None:
    """Main per-vessel coroutine. Lives for the entire trading session."""
    sid = spec.strategy_id
    idx = spec.instrument_id
    log = logger.bind(engine="strategy", sid=sid, idx=idx)
    log.info("vessel: starting")

    # ── Prepare phase ────────────────────────────────────────────────────
    spec.strategy.prepare(spec.context)
    memory: VesselMemory = spec.strategy.create_memory(spec.context)

    # Initialize state to FLAT if nothing in Redis yet
    set_state(redis_sync, sid, idx, read_state(redis_sync, sid, idx))

    dirty = asyncio.Event()

    # ── Lifecycle: wait-for-ready -> pre_open -> live ────────────────────
    # (No phase keys — Step 3 removed strategy:{sid}:{idx}:phase* as dead
    # schema; lifecycle progress is visible via logs + last_decision_ts.)

    # Wait for system ready + enabled flag.
    while not shutdown.is_set():
        if _decode(redis_sync.get(K.SYSTEM_FLAGS_READY)) == "true" and is_enabled(
            redis_sync, sid, idx
        ):
            break
        await asyncio.sleep(1.0)

    if shutdown.is_set():
        return

    spec.strategy.on_pre_open(spec.context)

    # ── Universe maintenance (strategy-owned subscription policy) ────────
    async def ensure_universe(market: MarketView) -> None:
        update = spec.strategy.update_universe(spec.context, memory, market)
        if update is None:
            return
        if update.basket_view is not None:
            redis_sync.set(K.vessel_basket(sid, idx), orjson.dumps(update.basket_view))
        for tok in update.subscribe:
            redis_sync.sadd(K.MARKET_DATA_SUBSCRIPTIONS_DESIRED, tok)
            router.register(tok, dirty)
        for tok in update.unsubscribe:
            router.unregister(tok, dirty)
        await router.reconcile()
        log.info(
            f"universe: {update.reason} subscribe={len(update.subscribe)} "
            f"unsubscribe={len(update.unsubscribe)}"
        )

    await ensure_universe(_read_market_view(redis_sync, idx))

    # ── LIVE loop ────────────────────────────────────────────────────────
    config_reload_at = time.time() + 60.0  # reload config every 60s
    while not shutdown.is_set():
        # Wait for a tick on any of our subscribed tokens.
        # Idle wakeups (timeout) still run the session-end / cooldown /
        # config-reload checks below.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(dirty.wait(), timeout=2.0)
        dirty.clear()

        # Check session-end (15:30 IST).
        now_ist = datetime.now(_IST)
        hhmm = f"{now_ist.hour:02d}:{now_ist.minute:02d}"
        if hhmm >= "15:30":
            log.info("vessel: session close reached — draining")
            spec.strategy.on_drain(spec.context)
            break

        # Vessel-level enable check (operator can flip enabled=false to halt).
        if not is_enabled(redis_sync, sid, idx):
            await asyncio.sleep(1.0)
            continue

        # Cooldown -> FLAT auto-transition.
        maybe_exit_cooldown(redis_sync, sid, idx)

        # State gate.
        state = read_state(redis_sync, sid, idx)
        if state == "HALTED":
            await asyncio.sleep(2.0)
            continue

        # Memory ↔ Redis state sync. Redis is the source of truth (order-exec
        # writes state on confirmed fill; init resets it on boot). On any
        # mismatch, trust Redis: clear or reload memory.held_* from the
        # position record so the strategy takes the correct branch
        # (entry-gates when FLAT, continuation when IN_CE/IN_PE).
        if state in ("FLAT", "COOLDOWN") and (memory.held_side or memory.held_token):
            log.warning(
                f"vessel state desync: redis={state} memory.held_side={memory.held_side}; "
                "clearing memory"
            )
            memory.held_side = None
            memory.held_token = None
            memory.held_strike = None
        elif state in ("IN_CE", "IN_PE"):
            target_side = "CE" if state == "IN_CE" else "PE"
            if memory.held_side != target_side or not memory.held_token:
                pos_id = redis_sync.get(K.vessel_current_position_id(sid, idx))
                if isinstance(pos_id, bytes):
                    pos_id = pos_id.decode()
                if pos_id:
                    pos_hash = redis_sync.hgetall(K.orders_position(pos_id)) or {}
                    pos_decoded = {
                        (k.decode() if isinstance(k, bytes) else k): (
                            v.decode() if isinstance(v, bytes) else v
                        )
                        for k, v in pos_hash.items()
                    }
                    memory.held_side = target_side
                    memory.held_token = pos_decoded.get("instrument_token") or None
                    strike_raw = pos_decoded.get("strike")
                    memory.held_strike = int(strike_raw) if strike_raw else None
                    log.info(
                        f"vessel sync: redis state={state} pos={pos_id} "
                        f"token={memory.held_token} strike={memory.held_strike}"
                    )

        # Periodic config hot-reload (Strategy.md §10.3).
        if time.time() >= config_reload_at:
            reload_vessel_config(redis_sync, spec)
            spec.strategy.on_config_reload(spec.context, memory)
            config_reload_at = time.time() + 60.0

        # One consistent market read per evaluation; shared by the universe
        # check and the snapshot build (no double Redis round-trip).
        market = _read_market_view(redis_sync, idx)

        # Universe policy (e.g. ATM shift). Pure compute on the view; the
        # strategy's own hysteresis keeps this cheap on every tick.
        await ensure_universe(market)

        # Strategy-defined snapshot.
        snapshot = spec.strategy.build_snapshot(spec.context, memory, market)

        # Strategy decision (pure)
        try:
            action = spec.strategy.on_tick(spec.context, snapshot, memory)
        except Exception as exc:
            log.exception(f"strategy.on_tick raised: {exc!r}")
            action = Action(ActionKind.NO_OP, reason=f"strategy_exception:{exc!r}")

        # Persist metrics + decision telemetry
        await _persist_metrics_and_decision(redis_async, sid=sid, instrument_id=idx, action=action)
        decision_log.emit(sid, idx, snapshot, action, state=state)

        # Apply action (state transition + signal)
        if state in ("FLAT", "IN_CE", "IN_PE"):
            await _apply_action(
                redis_async,
                redis_sync,
                spec=spec,
                action=action,
                memory=memory,
            )
        elif state == "COOLDOWN":
            # Telemetry only during cooldown — no signals.
            pass

    # ── DRAIN ────────────────────────────────────────────────────────────
    log.info("vessel: stopped")
