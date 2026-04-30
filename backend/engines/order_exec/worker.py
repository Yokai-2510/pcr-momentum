"""
engines.order_exec.worker — per-signal lifecycle orchestrator.

The worker thread pulls a Signal off the work queue and runs it through all
six stages from Strategy.md §10:

  A  pre_entry_gate           reads-only sanity gates
  B  entry.submit_and_monitor place + monitor (paper or live)
  C  (folded into B)
  D  exit_eval (loop)         8-trigger cascade ticked from live chain leaf
  E  exit_submit              modify-only SELL (paper or live)
  F  reporting + cleanup      ClosedPositionReport → Postgres → Lua cleanup

Each stage updates `orders:status:{pos_id}` HASH so observers (FastAPI,
Health, dashboards) can follow progress.
"""

from __future__ import annotations

import asyncio
import queue
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import orjson
import redis as _redis_sync
from loguru import logger

from engines.order_exec import (
    cleanup,
    exit_eval,
    exit_submit,
    pre_entry_gate,
    reporting,
)
from engines.order_exec import (
    entry as entry_mod,
)
from state import keys as K
from state.schemas.position import ExitProfile, Position, PositionStage
from state.schemas.report import MarketSnapshot
from state.schemas.signal import Signal

_IST = ZoneInfo("Asia/Kolkata")

EXIT_POLL_SLEEP_SEC = 0.5  # how often to re-evaluate exit cascade


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _now_hhmm() -> str:
    n = datetime.now(_IST).time()
    return f"{n.hour:02d}:{n.minute:02d}"


def _now_ts_ms() -> int:
    return int(time.time() * 1000)


def _read_json(redis_sync: _redis_sync.Redis, key: str) -> Any:
    raw = redis_sync.get(key)
    if not raw:
        return None
    blob = raw if isinstance(raw, bytes) else raw.encode()
    return orjson.loads(blob)


def _read_index_config(redis_sync: _redis_sync.Redis, index: str) -> dict[str, Any]:
    parsed = _read_json(redis_sync, K.strategy_config_index(index))
    return parsed if isinstance(parsed, dict) else {}


def _read_leaf(
    redis_sync: _redis_sync.Redis, index: str, token: str
) -> dict[str, Any] | None:
    chain = _read_json(redis_sync, K.market_data_index_option_chain(index))
    if not isinstance(chain, dict):
        return None
    for _strike, sides in chain.items():
        if not isinstance(sides, dict):
            continue
        for side in ("ce", "pe"):
            leaf = sides.get(side)
            if isinstance(leaf, dict) and leaf.get("token") == token:
                return leaf
    return None


def _read_mode(redis_sync: _redis_sync.Redis) -> str:
    raw = redis_sync.get("system:flags:mode_today") or redis_sync.get(K.SYSTEM_FLAGS_MODE)
    return _decode(raw) or "paper"


def _read_access_token(redis_sync: _redis_sync.Redis) -> str:
    payload = _read_json(redis_sync, K.USER_AUTH_ACCESS_TOKEN)
    if isinstance(payload, dict):
        return str(payload.get("token") or "")
    return _decode(payload)


def _persist_status(redis_sync: _redis_sync.Redis, pos_id: str, stage: PositionStage,
                    note: str = "") -> None:
    redis_sync.hset(
        K.orders_status(pos_id),
        mapping={
            "stage": stage.value,
            "note": note,
            "ts_ms": str(_now_ts_ms()),
        },
    )


def _build_market_snapshot(
    redis_sync: _redis_sync.Redis, index: str, signal: Signal
) -> MarketSnapshot:
    spot = 0.0
    spot_hash = redis_sync.hgetall(K.market_data_index_spot(index))
    if spot_hash:
        try:
            spot = float(_decode(spot_hash.get(b"ltp") or spot_hash.get("ltp") or 0))
        except (TypeError, ValueError):
            spot = 0.0
    return MarketSnapshot(
        ts=datetime.now(UTC),
        spot=spot,
        sum_ce=float(signal.sum_ce_at_signal),
        sum_pe=float(signal.sum_pe_at_signal),
        delta=float(signal.delta_at_signal),
        delta_pcr_cumulative=signal.delta_pcr_at_signal,
        per_strike={},
    )


def _record_rejected_signal(
    redis_sync: _redis_sync.Redis, signal: Signal, reason: str
) -> None:
    redis_sync.xadd(
        K.STRATEGY_STREAM_REJECTED_SIGNALS,
        {"sig_id": signal.sig_id, "index": signal.index, "reason": reason},
        maxlen=10_000,
        approximate=True,
    )


def process_signal(
    redis_sync: _redis_sync.Redis,
    pool: asyncpg.Pool | None,
    signal: Signal,
) -> None:
    """Run the full A→F pipeline for one signal. Best-effort: never raises."""
    log = logger.bind(engine="order_exec", index=signal.index, sig_id=signal.sig_id)

    pos_id = f"P-{uuid.uuid4().hex[:12]}"
    signal_received_ts_ms = _now_ts_ms()

    # ── STAGE A: pre-entry gate ─────────────────────────────────────────
    _persist_status(redis_sync, pos_id, PositionStage.GATE_PREENTRY)
    ok, reason = pre_entry_gate.check(redis_sync, signal)
    if not ok:
        log.warning(f"pre_entry_gate rejected: {reason}")
        _persist_status(redis_sync, pos_id, PositionStage.ABORTED, reason)
        _record_rejected_signal(redis_sync, signal, reason)
        return

    # Read mode + lot_size + index meta
    mode = _read_mode(redis_sync)
    access_token = _read_access_token(redis_sync) if mode == "live" else ""
    cfg_idx = _read_index_config(redis_sync, signal.index)
    lot_size = int(cfg_idx.get("lot_size") or 1)
    sl_pct = float(cfg_idx.get("sl_pct") or 0.20)
    target_pct = float(cfg_idx.get("target_pct") or 0.50)
    tsl_arm_pct = float(cfg_idx.get("tsl_arm_pct") or 0.15)
    tsl_trail_pct = float(cfg_idx.get("tsl_trail_pct") or 0.05)
    max_hold_sec = int(cfg_idx.get("max_hold_sec") or 1500)

    market_snapshot_entry = _build_market_snapshot(redis_sync, signal.index, signal)
    pre_open_snapshot = _read_json(redis_sync, K.strategy_pre_open(signal.index)) or {}
    signal_snapshot = signal.model_dump(mode="json")

    # ── STAGE B + C: entry ──────────────────────────────────────────────
    _persist_status(redis_sync, pos_id, PositionStage.ENTRY_SUBMITTING)
    entry_result = entry_mod.submit_and_monitor(
        redis_sync, signal, pos_id,
        mode=mode, access_token=access_token, lot_size=lot_size,
    )
    if entry_result.abandon_reason or entry_result.filled_qty <= 0:
        log.warning(f"entry abandoned: {entry_result.abandon_reason}")
        _persist_status(redis_sync, pos_id, PositionStage.ABORTED, entry_result.abandon_reason or "no_fill")
        _record_rejected_signal(redis_sync, signal, entry_result.abandon_reason or "no_fill")
        return

    _persist_status(redis_sync, pos_id, PositionStage.ENTRY_FILLED)

    # Build the Position object that exit_eval / persistence consume.
    entry_price = float(entry_result.avg_fill_price)
    sl_level = round(entry_price * (1.0 - sl_pct), 4)
    target_level = round(entry_price * (1.0 + target_pct), 4)

    position = Position(
        pos_id=pos_id,
        sig_id=signal.sig_id,
        index=signal.index,
        side=signal.side,
        strike=int(signal.strike),
        instrument_token=signal.instrument_token,
        qty=int(entry_result.filled_qty),
        entry_order_id=entry_result.order_id,
        exit_order_id=None,
        entry_price=entry_price,
        entry_ts=datetime.now(UTC),
        exit_price=None,
        exit_ts=None,
        mode=mode,  # type: ignore[arg-type]
        intent=signal.intent.value if hasattr(signal.intent, "value") else str(signal.intent),  # type: ignore[arg-type]
        sl_level=sl_level,
        target_level=target_level,
        tsl_armed=False,
        tsl_arm_pct=tsl_arm_pct,
        tsl_trail_pct=tsl_trail_pct,
        tsl_level=None,
        peak_premium=entry_price,
        current_premium=entry_price,
        pnl=0.0,
        pnl_pct=0.0,
        holding_seconds=0,
        exit_profile=ExitProfile(
            sl_pct=sl_pct,
            target_pct=target_pct,
            tsl_arm_pct=tsl_arm_pct,
            tsl_trail_pct=tsl_trail_pct,
            max_hold_sec=max_hold_sec,
        ),
        sum_ce_at_entry=float(signal.sum_ce_at_signal),
        sum_pe_at_entry=float(signal.sum_pe_at_signal),
        delta_pcr_at_entry=signal.delta_pcr_at_signal,
        strategy_version=signal.strategy_version,
    )

    # Persist the Position HASH + membership sets.
    pipe = redis_sync.pipeline()
    pipe.hset(
        K.orders_position(pos_id),
        mapping={k: orjson.dumps(v).decode() if isinstance(v, dict | list)
                 else (v.isoformat() if isinstance(v, datetime) else str(v))
                 for k, v in position.model_dump(mode="json").items()
                 if v is not None},
    )
    pipe.sadd(K.ORDERS_POSITIONS_OPEN, pos_id)
    pipe.sadd(K.orders_positions_open_by_index(signal.index), pos_id)
    pipe.set(K.strategy_current_position_id(signal.index), pos_id)
    pipe.execute()

    # ── STAGE D: exit eval loop ─────────────────────────────────────────
    _persist_status(redis_sync, pos_id, PositionStage.EXIT_EVAL)
    exit_eval_history: list[dict[str, Any]] = []
    trailing_history: list[dict[str, Any]] = []
    exit_reason_resolved = None
    decision_ts_ms = 0

    while True:
        leaf = _read_leaf(redis_sync, signal.index, signal.instrument_token)
        cur_premium = float(leaf.get("ltp") or 0) if leaf else position.current_premium
        if cur_premium <= 0:
            cur_premium = position.current_premium

        position = exit_eval.update_trailing_state(position, current_premium=cur_premium)
        if position.tsl_armed:
            trailing_history.append({
                "ts_ms": _now_ts_ms(),
                "peak": position.peak_premium,
                "tsl_level": position.tsl_level,
            })

        daily_loss = _decode(redis_sync.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)) == "true"
        should_exit, reason_enum = exit_eval.evaluate(
            position,
            current_premium=cur_premium,
            current_leaf=leaf,
            now_ts_ms=_now_ts_ms(),
            now_hhmm=_now_hhmm(),
            daily_loss_circuit_triggered=daily_loss,
        )
        exit_eval_history.append({
            "ts_ms": _now_ts_ms(),
            "premium": cur_premium,
            "should_exit": should_exit,
            "reason": reason_enum.value if reason_enum else None,
        })
        if should_exit:
            exit_reason_resolved = reason_enum
            decision_ts_ms = _now_ts_ms()
            break
        # Manual-exit signal? (Phase 9 will plumb /commands/manual_exit.)
        time.sleep(EXIT_POLL_SLEEP_SEC)

    assert exit_reason_resolved is not None  # type guard

    # ── STAGE E: exit submit ────────────────────────────────────────────
    _persist_status(redis_sync, pos_id, PositionStage.EXIT_SUBMITTING, exit_reason_resolved.value)
    exit_result = exit_submit.submit_and_complete(
        redis_sync, position, exit_reason_resolved.value,
        mode=mode, access_token=access_token, now_hhmm=_now_hhmm(),
    )
    exit_result.decision_to_exit_submit_ms = max(0, _now_ts_ms() - decision_ts_ms)
    _persist_status(redis_sync, pos_id, PositionStage.EXIT_FILLED)

    # ── STAGE F: reporting + cleanup ────────────────────────────────────
    _persist_status(redis_sync, pos_id, PositionStage.REPORTING)
    market_snapshot_exit = _build_market_snapshot(redis_sync, signal.index, signal)

    report = reporting.build_report(
        position=position,
        entry=entry_result,
        exit_result=exit_result,
        exit_reason=exit_reason_resolved,
        market_snapshot_entry=market_snapshot_entry,
        market_snapshot_exit=market_snapshot_exit,
        pre_open_snapshot=pre_open_snapshot if isinstance(pre_open_snapshot, dict) else {},
        signal_snapshot=signal_snapshot,
        signal_to_submit_ms=max(0, signal_received_ts_ms - signal_received_ts_ms),
        exit_eval_history=exit_eval_history,
        trailing_history=trailing_history or None,
    )

    if pool is not None:
        try:
            asyncio.run(reporting.persist_report(pool, report))
        except Exception as e:
            log.exception(f"persist_report failed; buffering: {e!r}")
            redis_sync.rpush(
                "orders:reports:pending",
                orjson.dumps(report.model_dump(mode="json")).decode(),
            )

    _persist_status(redis_sync, pos_id, PositionStage.CLEANUP)
    order_ids = [entry_result.order_id, exit_result.order_id]
    try:
        cleanup.cleanup(
            redis_sync,
            pos_id=pos_id,
            sig_id=signal.sig_id,
            order_ids=order_ids,
            index=signal.index,
        )
    except Exception as e:
        log.exception(f"cleanup raised (non-fatal): {e!r}")

    _persist_status(redis_sync, pos_id, PositionStage.DONE)
    log.info(
        f"order_exec[{signal.index}]: closed pos={pos_id} reason={exit_reason_resolved.value} "
        f"pnl=₹{report.pnl:.2f} ({report.pnl_pct:+.2f}%)"
    )


def worker_loop(
    work_queue: queue.Queue,
    redis_sync: _redis_sync.Redis,
    pool: asyncpg.Pool | None,
) -> None:
    """Block on the queue; for each signal, run process_signal()."""
    log = logger.bind(engine="order_exec", thread="worker")
    while True:
        item = work_queue.get()
        if item is None:
            log.info("worker_loop: shutdown sentinel received; exiting")
            work_queue.task_done()
            return
        signal: Signal = item
        try:
            process_signal(redis_sync, pool, signal)
        except Exception as e:
            log.exception(f"worker failed on {signal.sig_id}: {e!r}")
        finally:
            work_queue.task_done()
