"""
Vessel runner — one async task per (strategy_id, instrument_id) pair.

Owns ALL Redis I/O:
  - reads option_chain + spot -> builds Snapshot
  - calls strategy.on_tick(ctx, snapshot, memory) -> Action
  - writes per-tick decision telemetry (Strategy.md §11.1)
  - applies state transitions
  - calls publisher.emit_signal(...) on actionable Actions
  - updates basket subscriptions when ATM shifts

The runner is generic — the same code drives every Strategy implementation.
What varies between strategies is the Strategy class injected into the
runner; the runner doesn't care.

Loop shape (Strategy.md §2.3 — event-driven, no artificial floor):

    while True:
        await dirty.wait()         # blocks at OS level when idle
        dirty.clear()              # reset BEFORE reading state
        snapshot = build_snapshot()
        action = strategy.on_tick(ctx, snapshot, memory)
        await persist_metrics(action)
        await apply_action(action)
"""

from __future__ import annotations

import asyncio
import time
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
from engines.strategy.strategies.base import Action, ActionKind
from engines.strategy.strategies.bid_ask_imbalance.basket import (
    Basket,
    maybe_shift_basket,
)
from engines.strategy.strategies.bid_ask_imbalance.buffer import BufferStore
from engines.strategy.strategies.bid_ask_imbalance.decisions import timing as timing_mod
from engines.strategy.strategies.bid_ask_imbalance.snapshot import build_snapshot
from engines.strategy.strategies.bid_ask_imbalance.state import (
    enter_cooldown,
    halt,
    is_enabled,
    maybe_exit_cooldown,
    read_state,
    set_state,
)
from engines.strategy.strategies.bid_ask_imbalance.strategy import MemoryStore
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
            out[kk] = float(vv) if "." in vv or kk in {"ltp", "prev_close", "change_inr", "change_pct"} else int(vv)
        except ValueError:
            out[kk] = vv
    return out


def _read_meta(redis_sync: Any, index: str) -> dict[str, Any]:
    parsed = _read_json(redis_sync, K.market_data_index_meta(index))
    return parsed if isinstance(parsed, dict) else {}


def _build_token_lookup(meta: dict[str, Any], chain: dict[str, Any]):
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
        return leaf.get("token")

    return _lookup


async def _persist_metrics_and_decision(
    redis_async: _redis_async.Redis,
    *,
    sid: str,
    instrument_id: str,
    action: Action,
) -> None:
    """Write the last_decision telemetry block + per-strike/cumulative metrics.

    Always called (Strategy.md §5.1: every tick produces a logged decision,
    even NO_OP). This is what makes silent-loop bugs detectable.
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
    memory: MemoryStore,
) -> None:
    """Translate Action into state mutations + signal emission."""
    sid = spec.strategy_id
    idx = spec.instrument_id
    cooldown_sec = int(spec.context.instrument_config.get("post_sl_cooldown_sec", 60))
    rev_cooldown_sec = int(spec.context.instrument_config.get("post_reversal_cooldown_sec", 90))
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
    if action.kind == ActionKind.ENTER:
        await publisher.emit_signal(
            redis_async,
            strategy_id=sid,
            instrument_id=idx,
            action=action,
        )
        # No state write, no counter increment, no memory mutation.
        # The next-tick state-sync block will reflect order-exec's outcome.

    elif action.kind == ActionKind.FLIP:
        await publisher.emit_signal(
            redis_async,
            strategy_id=sid,
            instrument_id=idx,
            action=action,
        )
        # State + counters get written by order-exec on confirmed flip fill.

    elif action.kind == ActionKind.EXIT:
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

    instrument_cfg = spec.context.instrument_config or {}
    strategy_cfg = spec.context.strategy_config or {}

    strike_step = int(instrument_cfg.get("strike_step", 50))
    basket_size = int(instrument_cfg.get("basket_size", 5))
    hysteresis_sec = int(((strategy_cfg.get("atm_shift") or {}).get("hysteresis_sec")) or 5)
    buffer_capacity = int(((strategy_cfg.get("buffer") or {}).get("ring_size")) or 50)

    memory = MemoryStore(
        buffers=BufferStore(capacity=buffer_capacity),
        basket=Basket(atm=0),
        timing_windows=timing_mod.parse_windows(strategy_cfg.get("time_windows") or []),
    )

    # Initialize state to FLAT if nothing in Redis yet
    set_state(redis_sync, sid, idx, read_state(redis_sync, sid, idx))

    dirty = asyncio.Event()

    # ── Phase: BOOT -> PRE_OPEN -> SETTLE -> LIVE ────────────────────────
    redis_sync.set(K.vessel_phase(sid, idx), "BOOT")
    redis_sync.set(K.vessel_phase_entered_ts(sid, idx), str(int(time.time() * 1000)))

    # Wait for system ready + enabled flag.
    while not shutdown.is_set():
        if _decode(redis_sync.get(K.SYSTEM_FLAGS_READY)) == "true" and is_enabled(redis_sync, sid, idx):
            break
        await asyncio.sleep(1.0)

    if shutdown.is_set():
        return

    redis_sync.set(K.vessel_phase(sid, idx), "PRE_OPEN")
    spec.strategy.on_pre_open(spec.context)

    redis_sync.set(K.vessel_phase(sid, idx), "LIVE")
    redis_sync.set(K.vessel_phase_entered_ts(sid, idx), str(int(time.time() * 1000)))

    # ── Initial basket build ─────────────────────────────────────────────
    last_basket_check_ms = 0

    async def ensure_basket() -> None:
        nonlocal last_basket_check_ms
        now_ms = int(time.time() * 1000)
        # Re-read instruments + spot fresh on every basket-check.
        spot_hash = _read_spot_hash(redis_sync, idx)
        chain = _read_json(redis_sync, K.market_data_index_option_chain(idx)) or {}
        spot = spot_hash.get("ltp")
        token_lookup = _build_token_lookup(_read_meta(redis_sync, idx), chain)
        transition = maybe_shift_basket(
            current=memory.basket,
            spot=spot,
            strike_step=strike_step,
            basket_size=basket_size,
            now_ms=now_ms,
            hysteresis_sec=hysteresis_sec,
            token_lookup=token_lookup,
        )
        if transition is None:
            return
        # Apply transition.
        memory.basket = transition.new_basket
        memory.buffers.discard(transition.dropped_tokens)
        # Update vessel basket key.
        redis_sync.set(
            K.vessel_basket(sid, idx),
            orjson.dumps({
                "atm": transition.new_basket.atm,
                "ce": [transition.new_basket.ce_tokens.get(s) for s in transition.new_basket.ce_strikes],
                "pe": [transition.new_basket.pe_tokens.get(s) for s in transition.new_basket.pe_strikes],
            }),
        )
        # Update subscriptions
        for tok in transition.added_tokens:
            redis_sync.sadd(K.MARKET_DATA_SUBSCRIPTIONS_DESIRED, tok)
            router.register(tok, dirty)
        for tok in transition.dropped_tokens:
            router.unregister(tok, dirty)
        await router.reconcile()
        log.info(
            f"basket: {transition.reason} added={len(transition.added_tokens)} "
            f"dropped={len(transition.dropped_tokens)} atm={transition.new_basket.atm}"
        )
        last_basket_check_ms = now_ms

    await ensure_basket()

    # ── LIVE loop ────────────────────────────────────────────────────────
    config_reload_at = time.time() + 60.0  # reload config every 60s
    while not shutdown.is_set():
        # Wait for a tick on any of our basket tokens.
        try:
            await asyncio.wait_for(dirty.wait(), timeout=2.0)
        except (TimeoutError, asyncio.TimeoutError):
            # Idle wakeup — still check phase end / cooldown / config reload.
            pass
        dirty.clear()

        # Check session-end (15:30 IST).
        now_ist = datetime.now(_IST)
        hhmm = f"{now_ist.hour:02d}:{now_ist.minute:02d}"
        if hhmm >= "15:30":
            log.info("vessel: session close reached")
            redis_sync.set(K.vessel_phase(sid, idx), "DRAIN")
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
                pos_id = redis_sync.get(K.strategy_current_position_id(idx))
                if isinstance(pos_id, bytes):
                    pos_id = pos_id.decode()
                if pos_id:
                    pos_hash = redis_sync.hgetall(K.orders_position(pos_id)) or {}
                    pos_decoded = {
                        (k.decode() if isinstance(k, bytes) else k):
                        (v.decode() if isinstance(v, bytes) else v)
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
            memory.timing_windows = timing_mod.parse_windows(
                (spec.context.strategy_config or {}).get("time_windows") or []
            )
            config_reload_at = time.time() + 60.0

        # Periodic basket re-check (every 1 s of wall time, regardless of dirty).
        if int(time.time() * 1000) - last_basket_check_ms > 1000:
            await ensure_basket()

        # Build snapshot. If a position is held but its strike has been
        # dropped from the basket by an ATM shift, pin it into the snapshot
        # so continuation / reversal evaluators have visibility on the
        # held leg (Strategy.md §3.2 + §5.3).
        ts_ms = int(time.time() * 1000)
        chain = _read_json(redis_sync, K.market_data_index_option_chain(idx)) or {}
        spot_hash = _read_spot_hash(redis_sync, idx)
        snapshot = build_snapshot(
            instrument_id=idx,
            atm=memory.basket.atm,
            basket_ce=memory.basket.ce_pairs(),
            basket_pe=memory.basket.pe_pairs(),
            option_chain=chain,
            spot=spot_hash,
            snapshot_ts=ts_ms,
            pinned_token=memory.held_token,
            pinned_side=memory.held_side,
            pinned_strike=memory.held_strike,
        )

        # Strategy decision (pure)
        try:
            action = spec.strategy.on_tick(spec.context, snapshot, memory)
        except Exception as exc:
            log.exception(f"strategy.on_tick raised: {exc!r}")
            action = Action(ActionKind.NO_OP, reason=f"strategy_exception:{exc!r}")

        # Persist metrics + decision telemetry
        await _persist_metrics_and_decision(
            redis_async, sid=sid, instrument_id=idx, action=action
        )
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
