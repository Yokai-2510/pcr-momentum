"""
engines.order_exec.worker — per-signal lifecycle orchestrator.

The worker thread pulls a Signal off the work queue and runs it through all
six stages from Strategy.md §10:

  A  pre_entry_gate           reads-only sanity gates + atomic allocator reserve
  B  entry.submit_and_monitor place + monitor (paper or live)
  C  (folded into B)
  D  exit_eval (loop)         8-trigger cascade ticked from live chain leaf
  E  exit_submit              modify-only SELL (paper or live)
  F  reporting + cleanup      ClosedPositionReport → buffer → Lua cleanup

REVERSAL_FLIP intent (Strategy.md §8.2): when a signal arrives with
`intent == REVERSAL_FLIP`, the worker first runs Stage E + F on the
currently-open position for the index (closing the prior leg) before
treating the signal as a fresh entry. This keeps the per-index
"only one open position at a time" invariant atomic from the worker's
point of view.

Reporting (Bug-1 fix): the worker no longer calls `asyncio.run(persist_report)`
because the worker thread doesn't own the asyncpg pool's loop. It pushes
the report payload to `orders:reports:pending` (Redis LIST) and the
Background engine drains the queue from its own loop.

Each stage updates `orders:status:{pos_id}` HASH so observers (FastAPI,
Health, dashboards) can follow progress.
"""

from __future__ import annotations

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
    allocator,
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
from state.schemas.position import ExitProfile, ExitReason, Position, PositionStage
from state.schemas.report import MarketSnapshot
from state.schemas.signal import Signal

_IST = ZoneInfo("Asia/Kolkata")

EXIT_POLL_SLEEP_SEC = 0.5  # how often to re-evaluate exit cascade

# Mutated-each-tick fields written back to orders:positions:{pos_id} HASH
# during the exit-eval loop. `pnl` and `pnl_pct` are recomputed in-loop so
# the position record always reflects the live mark-to-market.
_HASH_REFRESH_FIELDS = (
    "peak_premium",
    "tsl_armed",
    "tsl_level",
    "current_premium",
    "pnl",
    "pnl_pct",
    "holding_seconds",
)


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


def _serialize_position_field(value: Any) -> str:
    if isinstance(value, dict | list):
        return orjson.dumps(value).decode()
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _refresh_position_hash(
    redis_sync: _redis_sync.Redis, position: Position, fields: tuple[str, ...]
) -> None:
    """Bug-4: keep dashboards consistent by HSETing mutated fields each tick."""
    dump = position.model_dump(mode="json")
    mapping: dict[str, str] = {}
    for f in fields:
        if f in dump:
            mapping[f] = _serialize_position_field(dump[f])
    if not mapping:
        return
    try:
        redis_sync.hset(K.orders_position(position.pos_id), mapping=mapping)
    except Exception as e:  # pragma: no cover — best-effort observability
        logger.bind(pos_id=position.pos_id).warning(
            f"_refresh_position_hash failed: {e!r}"
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


def _buffer_report_for_persistence(
    redis_sync: _redis_sync.Redis, report_payload: dict[str, Any]
) -> None:
    """Bug-1 fix: never call asyncio.run from worker thread.

    The Background engine's report_drainer LPOPs this list and INSERTs into
    Postgres on its own event loop. The buffer is bounded so a stuck DB
    cannot OOM Redis: we trim from the head if it overflows.
    """
    try:
        redis_sync.rpush(
            K.ORDERS_REPORTS_PENDING,
            orjson.dumps(report_payload).decode(),
        )
        redis_sync.ltrim(K.ORDERS_REPORTS_PENDING, -10_000, -1)
    except Exception as e:  # pragma: no cover
        logger.bind(engine="order_exec").exception(
            f"buffer_report rpush failed: {e!r}"
        )


def _load_position_from_hash(
    redis_sync: _redis_sync.Redis, pos_id: str
) -> Position | None:
    """Re-hydrate a Position from `orders:positions:{pos_id}` HASH.

    Used by the REVERSAL_FLIP path (Bug-2 fix) to look up the prior leg's
    state so we can run Stage E + F on it before opening the new leg.
    """
    raw = redis_sync.hgetall(K.orders_position(pos_id))
    if not raw:
        return None
    decoded = {
        (k.decode() if isinstance(k, bytes) else k): (
            v.decode() if isinstance(v, bytes) else v
        )
        for k, v in raw.items()
    }
    typed: dict[str, Any] = {}
    for field, value in decoded.items():
        if not value:
            continue
        if field == "exit_profile":
            try:
                typed[field] = orjson.loads(value)
            except Exception:
                continue
        elif field in ("entry_ts", "exit_ts"):
            try:
                typed[field] = datetime.fromisoformat(value)
            except Exception:
                continue
        elif field == "tsl_armed":
            typed[field] = value.lower() == "true"
        elif field in (
            "qty",
            "strike",
            "holding_seconds",
        ):
            try:
                typed[field] = int(value)
            except ValueError:
                continue
        elif field in (
            "entry_price",
            "exit_price",
            "sl_level",
            "target_level",
            "tsl_level",
            "tsl_arm_pct",
            "tsl_trail_pct",
            "peak_premium",
            "current_premium",
            "pnl",
            "pnl_pct",
            "sum_ce_at_entry",
            "sum_pe_at_entry",
            "delta_pcr_at_entry",
        ):
            try:
                typed[field] = float(value)
            except ValueError:
                continue
        else:
            typed[field] = value
    try:
        return Position.model_validate(typed)
    except Exception as e:
        logger.bind(pos_id=pos_id).warning(
            f"_load_position_from_hash: validation failed: {e!r}"
        )
        return None


def _close_existing_position_for_flip(
    redis_sync: _redis_sync.Redis,
    index: str,
    *,
    mode: str,
    access_token: str,
    log: Any,
) -> bool:
    """REVERSAL_FLIP step 1 (Bug-2 fix): close the currently-open position
    on `index` cleanly before opening the new leg.

    Returns True when:
      - there was no current position (nothing to close), OR
      - the current position was successfully closed and cleaned up.
    Returns False when we found a current position but couldn't load /
    close it — the caller must abort the new entry to avoid double-open.
    """
    cur_pos_id = _decode(redis_sync.get(K.strategy_current_position_id(index)))
    if not cur_pos_id:
        return True

    log.info(f"REVERSAL_FLIP: closing prior position {cur_pos_id} on {index}")
    prior = _load_position_from_hash(redis_sync, cur_pos_id)
    if prior is None:
        log.warning(
            f"REVERSAL_FLIP: cannot hydrate prior position {cur_pos_id}; "
            f"skipping close, allocator may leak"
        )
        return False

    _persist_status(redis_sync, cur_pos_id, PositionStage.EXIT_SUBMITTING,
                    ExitReason.REVERSAL_FLIP.value)
    try:
        exit_result = exit_submit.submit_and_complete(
            redis_sync, prior, ExitReason.REVERSAL_FLIP.value,
            mode=mode, access_token=access_token, now_hhmm=_now_hhmm(),
        )
    except Exception as e:
        log.exception(f"REVERSAL_FLIP exit_submit failed for {cur_pos_id}: {e!r}")
        return False
    _persist_status(redis_sync, cur_pos_id, PositionStage.EXIT_FILLED)

    # Build a synthetic EntryResult for the prior leg — we don't have the
    # original entry_result in memory; reconstruct what reporting needs from
    # the persisted Position. Charges & latencies will be zero/best-effort;
    # the gross PnL computation is still correct.
    from engines.order_exec.entry import EntryResult as _EntryResult
    entry_result = _EntryResult(
        filled_qty=int(prior.qty),
        avg_fill_price=float(prior.entry_price),
        order_id=prior.entry_order_id,
        order_events=[],
        submit_to_ack_ms=0,
        ack_to_fill_ms=0,
    )

    market_snapshot = MarketSnapshot(
        ts=datetime.now(UTC),
        spot=0.0,
        sum_ce=float(prior.sum_ce_at_entry),
        sum_pe=float(prior.sum_pe_at_entry),
        delta=float(prior.sum_pe_at_entry - prior.sum_ce_at_entry),
        delta_pcr_cumulative=prior.delta_pcr_at_entry,
        per_strike={},
    )

    _persist_status(redis_sync, cur_pos_id, PositionStage.REPORTING)
    report = reporting.build_report(
        position=prior,
        entry=entry_result,
        exit_result=exit_result,
        exit_reason=ExitReason.REVERSAL_FLIP,
        market_snapshot_entry=market_snapshot,
        market_snapshot_exit=market_snapshot,
        pre_open_snapshot={},
        signal_snapshot={"intent": "REVERSAL_FLIP"},
        signal_to_submit_ms=0,
        exit_eval_history=None,
        trailing_history=None,
    )
    _buffer_report_for_persistence(redis_sync, report.model_dump(mode="json"))

    _persist_status(redis_sync, cur_pos_id, PositionStage.CLEANUP)
    try:
        cleanup.cleanup(
            redis_sync,
            pos_id=cur_pos_id,
            sig_id=prior.sig_id,
            order_ids=[prior.entry_order_id, exit_result.order_id],
            index=index,
        )
    except Exception as e:
        log.exception(f"REVERSAL_FLIP cleanup raised (non-fatal): {e!r}")

    # Release the prior leg's allocator reservation. The premium-required is
    # the same magnitude that was reserved at entry (we use entry_price * qty
    # as the canonical released figure since worst-case ask is no longer
    # observable here).
    released_premium = float(prior.entry_price) * float(prior.qty)
    allocator.release(
        redis_sync, index=index, premium_to_release_inr=released_premium
    )

    _persist_status(redis_sync, cur_pos_id, PositionStage.DONE)
    log.info(f"REVERSAL_FLIP: prior position {cur_pos_id} closed")
    return True


def process_signal(
    redis_sync: _redis_sync.Redis,
    pool: asyncpg.Pool | None,
    signal: Signal,
) -> None:
    """Run the full A→F pipeline for one signal. Best-effort: never raises.

    `pool` is retained for interface compatibility but is no longer used by
    the worker thread (Bug-1 fix). DB persistence is now performed by the
    Background engine's report_drainer.
    """
    del pool  # explicitly unused — see module docstring
    log = logger.bind(engine="order_exec", index=signal.index, sig_id=signal.sig_id)

    pos_id = f"P-{uuid.uuid4().hex[:12]}"
    signal_received_ts_ms = _now_ts_ms()

    mode = _read_mode(redis_sync)
    access_token = _read_access_token(redis_sync) if mode == "live" else ""

    # ── REVERSAL_FLIP pre-step ──────────────────────────────────────────
    intent_value = (
        signal.intent.value if hasattr(signal.intent, "value") else str(signal.intent)
    )
    if intent_value == "REVERSAL_FLIP":
        ok_flip = _close_existing_position_for_flip(
            redis_sync, signal.index,
            mode=mode, access_token=access_token, log=log,
        )
        if not ok_flip:
            _record_rejected_signal(redis_sync, signal, "reversal_close_failed")
            return

    # ── STAGE A: pre-entry gate + atomic allocator reserve ──────────────
    _persist_status(redis_sync, pos_id, PositionStage.GATE_PREENTRY)
    ok, reason, premium_reserved = pre_entry_gate.check_and_reserve(redis_sync, signal)
    if not ok:
        log.warning(f"pre_entry_gate rejected: {reason}")
        _persist_status(redis_sync, pos_id, PositionStage.ABORTED, reason)
        _record_rejected_signal(redis_sync, signal, reason)
        return

    # From here on, any abort path MUST release the allocator reservation.

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
        # Release the reservation we held for this attempt.
        allocator.release(
            redis_sync, index=signal.index, premium_to_release_inr=premium_reserved,
        )
        return

    _persist_status(redis_sync, pos_id, PositionStage.ENTRY_FILLED)

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
        intent=intent_value,  # type: ignore[arg-type]
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

    # Vessel-side state transition on confirmed entry fill. Order-exec is the
    # sole authoritative writer of vessel:state, current_position_id, and
    # the entries_today counter (Strategy.md §5.5 / §7).
    new_state = "IN_CE" if signal.side == "CE" else "IN_PE"

    pipe = redis_sync.pipeline()
    pipe.hset(
        K.orders_position(pos_id),
        mapping={k: _serialize_position_field(v)
                 for k, v in position.model_dump(mode="json").items()
                 if v is not None},
    )
    pipe.sadd(K.ORDERS_POSITIONS_OPEN, pos_id)
    pipe.sadd(K.orders_positions_open_by_index(signal.index), pos_id)
    pipe.set(K.strategy_current_position_id(signal.index), pos_id)
    pipe.set(K.strategy_state(signal.index), new_state)
    if signal.intent == "REVERSAL_FLIP":
        pipe.incr(K.strategy_counters_reversals_today(signal.index))
    pipe.incr(K.strategy_counters_entries_today(signal.index))
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

        # Live mark-to-market on the position record (read by /positions/open
        # and frontend dashboard) plus the global `orders:pnl:unrealized` sum.
        entry_price = float(position.entry_price or 0.0)
        qty = int(position.qty or 0)
        if entry_price > 0 and qty > 0:
            pnl_inr = round((cur_premium - entry_price) * qty, 4)
            pnl_pct = round((cur_premium / entry_price - 1.0) * 100.0, 4)
        else:
            pnl_inr = 0.0
            pnl_pct = 0.0
        holding_seconds = max(
            0,
            (_now_ts_ms() - int(position.entry_ts.timestamp() * 1000)) // 1000,
        )
        position = position.model_copy(update={
            "pnl": pnl_inr,
            "pnl_pct": pnl_pct,
            "holding_seconds": int(holding_seconds),
        })
        _refresh_position_hash(redis_sync, position, _HASH_REFRESH_FIELDS)

        if position.tsl_armed:
            trailing_history.append({
                "ts_ms": _now_ts_ms(),
                "peak": position.peak_premium,
                "tsl_level": position.tsl_level,
            })

        # Read the strategy-emitted exit-pull flag (Strategy.md §5.3 — exit
        # decisions emitted by the vessel land here as a per-position flag
        # rather than a separate code path; exit_eval honours them as
        # trigger #0).
        strategy_pull_raw = redis_sync.get(K.orders_exit_pull(pos_id))
        strategy_pull = _decode(strategy_pull_raw) or None

        daily_loss = _decode(redis_sync.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)) == "true"
        should_exit, reason_enum = exit_eval.evaluate(
            position,
            current_premium=cur_premium,
            current_leaf=leaf,
            now_ts_ms=_now_ts_ms(),
            now_hhmm=_now_hhmm(),
            daily_loss_circuit_triggered=daily_loss,
            strategy_exit_pull=strategy_pull,
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
    _buffer_report_for_persistence(redis_sync, report.model_dump(mode="json"))

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

    # Release the allocator slot now that the position is closed.
    allocator.release(
        redis_sync, index=signal.index, premium_to_release_inr=premium_reserved,
    )
    # Clear any strategy exit-pull flag now that we've acted on it.
    redis_sync.delete(K.orders_exit_pull(pos_id))

    # Vessel-side state transition on confirmed exit fill. Order-exec is the
    # sole authoritative writer of vessel:state and cooldown_*. Strategy.md §5.5.
    sid = signal.strategy_id or K.DEFAULT_STRATEGY_ID
    raw_instr = redis_sync.get(K.strategy_config_instrument(sid, signal.index))
    instr_cfg: dict[str, Any] = {}
    if raw_instr:
        try:
            instr_cfg = orjson.loads(raw_instr if isinstance(raw_instr, bytes) else raw_instr.encode())
        except Exception:
            instr_cfg = {}
    sl_cooldown = int(instr_cfg.get("post_sl_cooldown_sec", 60))
    flip_cooldown = int(instr_cfg.get("post_reversal_cooldown_sec", 90))
    strategy_exit_cooldown = int(instr_cfg.get("post_strategy_exit_cooldown_sec", 30))

    # Table-driven cooldown by exit reason. Anything not in the table → no
    # cooldown, vessel goes straight to FLAT. Centralizes the rule so adding
    # a new ExitReason only requires one row here.
    cooldown_by_reason: dict[str, tuple[int, str]] = {
        "HARD_SL":         (sl_cooldown,             "post_sl"),
        "TRAILING_SL":     (sl_cooldown,             "post_tsl"),
        "REVERSAL_FLIP":   (flip_cooldown,           "post_flip"),
        "STRATEGY_EXIT":   (strategy_exit_cooldown,  "post_strategy_exit"),
    }
    reason_str = exit_reason_resolved.value
    cooldown_sec, cooldown_reason = cooldown_by_reason.get(reason_str, (0, ""))
    next_state = "COOLDOWN" if cooldown_sec > 0 else "FLAT"

    pipe = redis_sync.pipeline()
    pipe.set(K.strategy_state(signal.index), next_state)
    if cooldown_sec > 0:
        pipe.set(K.strategy_cooldown_until_ts(signal.index), str(_now_ts_ms() + cooldown_sec * 1000))
        pipe.set(K.strategy_cooldown_reason(signal.index), cooldown_reason)
    else:
        pipe.set(K.strategy_cooldown_until_ts(signal.index), "0")
        pipe.set(K.strategy_cooldown_reason(signal.index), "")
    if report.pnl > 0:
        pipe.incr(K.strategy_counters_wins_today(signal.index))
    pipe.execute()

    _persist_status(redis_sync, pos_id, PositionStage.DONE)
    log.info(
        f"order_exec[{signal.index}]: closed pos={pos_id} reason={exit_reason_resolved.value} "
        f"pnl=₹{report.pnl:.2f} ({report.pnl_pct:+.2f}%) -> state={next_state}"
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
