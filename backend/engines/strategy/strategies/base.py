"""Per-index strategy thread for the premium-diff state machine.

One StrategyInstance runs per index. The instance reads the locked basket,
pre-open baseline, and live option-chain leaves from Redis; computes premium
diffs; updates live strategy telemetry; and emits typed Signal payloads for
Order Exec.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import orjson
import redis as _redis_sync
from loguru import logger

from engines.strategy import decision, pipeline, pre_open_snapshot, premium_diff
from state import keys as K
from state.schemas.config import IndexConfig
from state.schemas.signal import Signal, SignalIntent
from state.schemas.view import StrategyView

_IST = ZoneInfo("Asia/Kolkata")

PRE_OPEN_SNAPSHOT_HHMM = "09:14:50"
SETTLE_WINDOW_END_HHMM = "09:15:09"
ENTRY_FREEZE_HHMM = "15:10"
SESSION_CLOSE_HHMM = "15:30"
TICK_BLOCK_MS = 500

StrategyState = Literal["FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"]
OptionSide = Literal["CE", "PE"]

_VALID_STATES: set[str] = {"FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"}


def _now_ist() -> datetime:
    return datetime.now(_IST)


def _hhmm(t: dt_time | datetime | None = None) -> str:
    if t is None:
        t = _now_ist().time()
    elif isinstance(t, datetime):
        t = t.timetz()
    return f"{t.hour:02d}:{t.minute:02d}"


def _hhmmss() -> str:
    n = _now_ist().time()
    return f"{n.hour:02d}:{n.minute:02d}:{n.second:02d}"


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes | bytearray | str):
        return orjson.loads(raw)
    return raw


class StrategyInstance:
    """Single-thread per-index strategy."""

    index: str = ""

    def __init__(self, redis_sync: _redis_sync.Redis, config: IndexConfig) -> None:
        if not self.index:
            raise RuntimeError("StrategyInstance subclass must set `index`")
        self.redis = redis_sync
        self.config = config
        self.strike_step = int(config.strike_step)
        self.lot_size = int(config.lot_size)
        self._shutdown = False
        self._tick_seq = 0
        self._consumer_name = f"strategy:{self.index}"
        self._stream = K.market_data_stream_tick(self.index)
        self._group = f"strategy:{self.index}"
        self._strategy_version = self._read_str(K.SYSTEM_LIFECYCLE_GIT_SHA) or "unknown"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def request_shutdown(self) -> None:
        self._shutdown = True

    def run(self) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        try:
            log.info(f"strategy[{self.index}]: starting")
            self._wait_for_system_ready()
            if self._shutdown:
                return
            if not self._is_index_enabled():
                log.warning(f"strategy[{self.index}]: index disabled at boot")
                return
            self._wait_for_pre_open_snapshot_time()
            if self._shutdown:
                return
            self._capture_pre_open_snapshot()
            if not self._is_index_enabled():
                log.warning(f"strategy[{self.index}]: pre-open validation disabled index")
                return
            self._wait_for_settle_window_end()
            if self._shutdown:
                return
            self._enter_continuous_loop()
        except Exception as exc:
            log.exception(f"strategy[{self.index}]: fatal: {exc!r}")
            self._set_state("HALTED")
        finally:
            self._mark_exited()

    def _wait_for_system_ready(self) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        while not self._shutdown:
            if self._read_str(K.SYSTEM_FLAGS_READY) == "true":
                log.info(f"strategy[{self.index}]: init ready")
                return
            time.sleep(1.0)

    def _wait_for_pre_open_snapshot_time(self) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        while not self._shutdown:
            if _hhmmss() >= PRE_OPEN_SNAPSHOT_HHMM:
                log.info(f"strategy[{self.index}]: at/past pre-open snapshot time")
                return
            time.sleep(1.0)

    def _capture_pre_open_snapshot(self) -> None:
        result = pre_open_snapshot.capture(self.redis, self.index)
        if not result.get("valid"):
            self._enter_halted("pre_open_invalid")

    def _wait_for_settle_window_end(self) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        self._set_state("FLAT")
        while not self._shutdown:
            if _hhmmss() >= SETTLE_WINDOW_END_HHMM:
                log.info(f"strategy[{self.index}]: settle window done")
                return
            time.sleep(0.5)

    def _enter_continuous_loop(self) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        self._ensure_consumer_group()

        while not self._shutdown:
            self._heartbeat()
            if _hhmm() >= SESSION_CLOSE_HHMM:
                log.info(f"strategy[{self.index}]: session close reached")
                return

            self._maybe_exit_cooldown()

            try:
                resp = cast(
                    list[tuple[Any, list[tuple[Any, dict[str, Any]]]]] | None,
                    self.redis.xreadgroup(
                        self._group,
                        self._consumer_name,
                        {self._stream: ">"},
                        count=1,
                        block=TICK_BLOCK_MS,
                    ),
                )
            except Exception as exc:
                log.warning(f"xreadgroup failed: {exc!r}")
                time.sleep(0.2)
                continue

            if not resp:
                continue

            try:
                _stream_name, entries = resp[0]
                for entry_id, fields in entries:
                    self._tick_seq += 1
                    self._on_tick(_decode(entry_id), fields)
                    self.redis.xack(self._stream, self._group, entry_id)
            except Exception as exc:
                log.exception(f"on_tick raised: {exc!r}")

    # ------------------------------------------------------------------
    # Per-tick path
    # ------------------------------------------------------------------

    def _on_tick(self, _entry_id: str, _fields: dict[str, Any]) -> None:
        state = self._get_state()
        if state == "HALTED":
            return
        if state == "COOLDOWN":
            self._maybe_exit_cooldown()
            state = self._get_state()
            if state == "COOLDOWN":
                return

        basket = self._read_basket()
        ce_tokens = basket.get("ce", []) or []
        pe_tokens = basket.get("pe", []) or []
        all_tokens = [*ce_tokens, *pe_tokens]
        if not all_tokens:
            return

        current = self._read_current_premiums(all_tokens)
        pre_open = self._read_pre_open_premiums(all_tokens)
        if not current or not pre_open:
            return

        diffs = premium_diff.compute_diffs(current, pre_open)
        sum_ce, sum_pe = premium_diff.compute_sums(diffs, ce_tokens, pe_tokens)
        delta = sum_pe - sum_ce
        self._persist_live_view(diffs, sum_ce, sum_pe, delta)

        if state == "FLAT":
            self._maybe_emit_entry(sum_ce, sum_pe, delta, diffs, ce_tokens, pe_tokens)
            return
        if state == "IN_CE":
            self._maybe_emit_flip(state, delta, diffs, pe_tokens)
            return
        if state == "IN_PE":
            self._maybe_emit_flip(state, delta, diffs, ce_tokens)

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _maybe_emit_entry(
        self,
        sum_ce: float,
        sum_pe: float,
        delta: float,
        diffs: dict[str, float],
        ce_tokens: list[str],
        pe_tokens: list[str],
    ) -> None:
        log = logger.bind(engine="strategy", index=self.index)

        if pipeline.in_entry_freeze(_hhmm(), ENTRY_FREEZE_HHMM):
            return

        ok, _reason = pipeline.system_gates_pass(self._read_system_snapshot())
        if not ok:
            return

        entries_today = self._read_int(K.strategy_counters_entries_today(self.index))
        reversals_today = self._read_int(K.strategy_counters_reversals_today(self.index))
        capped, _cap_reason = pipeline.at_daily_caps(
            entries_today,
            reversals_today,
            "FRESH_ENTRY",
            int(self.config.max_entries_per_day),
            int(self.config.max_reversals_per_day),
        )
        if capped:
            return

        outcome = decision.decide_when_flat(
            sum_ce,
            sum_pe,
            delta,
            float(self.config.reversal_threshold_inr),
            float(self.config.entry_dominance_threshold_inr),
        )
        if outcome in {"WAIT", "WAIT_RECOVERY"}:
            return

        side: OptionSide
        tokens: list[str]
        if outcome == "BUY_CE":
            side = "CE"
            tokens = ce_tokens
        else:
            side = "PE"
            tokens = pe_tokens

        token, diff_at_signal = premium_diff.pick_highest_diff_strike(diffs, tokens)
        if token is None:
            log.debug(f"entry decision={outcome} but no token to enter")
            return

        leaf = self._read_leaf(token)
        if leaf is None:
            return
        ok, reason = pipeline.liquidity_gate_pass(
            leaf,
            int(self.config.qty_lots),
            self.lot_size,
            self._read_spread_skip_pct(),
        )
        if not ok:
            log.info(f"entry liquidity-gate failed: {reason}")
            return

        strike = self._strike_for_token(token, leaf)
        sig_id = self._emit_signal(
            intent=SignalIntent.FRESH_ENTRY,
            side=side,
            strike=strike,
            instrument_token=token,
            diff_at_signal=diff_at_signal,
            sum_ce=sum_ce,
            sum_pe=sum_pe,
            delta=delta,
        )
        log.info(
            f"strategy[{self.index}]: emit FRESH_ENTRY {side} strike={strike} "
            f"diff={diff_at_signal:.2f} sum_ce={sum_ce:.2f} sum_pe={sum_pe:.2f} "
            f"delta={delta:.2f} sig={sig_id}"
        )
        self._incr_counter(K.strategy_counters_entries_today(self.index))
        self._set_state("IN_CE" if side == "CE" else "IN_PE")

    def _maybe_emit_flip(
        self,
        state: StrategyState,
        delta: float,
        diffs: dict[str, float],
        target_tokens: list[str],
    ) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        threshold = float(self.config.reversal_threshold_inr)

        new_side: OptionSide
        if state == "IN_CE":
            if decision.decide_when_in_ce(delta, threshold) != "FLIP_TO_PE":
                return
            new_side = "PE"
        else:
            if decision.decide_when_in_pe(delta, threshold) != "FLIP_TO_CE":
                return
            new_side = "CE"

        entries_today = self._read_int(K.strategy_counters_entries_today(self.index))
        reversals_today = self._read_int(K.strategy_counters_reversals_today(self.index))
        capped, _reason = pipeline.at_daily_caps(
            entries_today,
            reversals_today,
            "REVERSAL_FLIP",
            int(self.config.max_entries_per_day),
            int(self.config.max_reversals_per_day),
        )
        if capped:
            return

        token, diff_at_signal = premium_diff.pick_highest_diff_strike(diffs, target_tokens)
        if token is None:
            return
        leaf = self._read_leaf(token)
        if leaf is None:
            return
        ok, reason = pipeline.liquidity_gate_pass(
            leaf,
            int(self.config.qty_lots),
            self.lot_size,
            self._read_spread_skip_pct(),
        )
        if not ok:
            log.info(f"flip liquidity-gate failed: {reason}")
            return

        sum_ce = self._read_float(K.strategy_live_sum_ce(self.index))
        sum_pe = self._read_float(K.strategy_live_sum_pe(self.index))
        strike = self._strike_for_token(token, leaf)
        sig_id = self._emit_signal(
            intent=SignalIntent.REVERSAL_FLIP,
            side=new_side,
            strike=strike,
            instrument_token=token,
            diff_at_signal=diff_at_signal,
            sum_ce=sum_ce,
            sum_pe=sum_pe,
            delta=delta,
        )
        log.info(
            f"strategy[{self.index}]: emit REVERSAL_FLIP {state}->{new_side} "
            f"strike={strike} delta={delta:.2f} sig={sig_id}"
        )
        self._incr_counter(K.strategy_counters_reversals_today(self.index))
        self._incr_counter(K.strategy_counters_entries_today(self.index))
        self._set_state("IN_CE" if new_side == "CE" else "IN_PE")

    # ------------------------------------------------------------------
    # Cooldown / halt
    # ------------------------------------------------------------------

    def _enter_cooldown(self, reason: str, duration_sec: int) -> None:
        until_ts_ms = int(time.time() * 1000) + duration_sec * 1000
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(K.strategy_state(self.index), "COOLDOWN")
        pipe.set(K.strategy_cooldown_until_ts(self.index), str(until_ts_ms))
        pipe.set(K.strategy_cooldown_reason(self.index), reason)
        pipe.execute()

    def _maybe_exit_cooldown(self) -> None:
        if self._get_state() != "COOLDOWN":
            return
        until = self._read_int(K.strategy_cooldown_until_ts(self.index))
        outcome = decision.decide_when_cooldown(int(time.time() * 1000), until)
        if outcome == "GO_FLAT":
            self._set_state("FLAT")

    def _enter_halted(self, reason: str) -> None:
        log = logger.bind(engine="strategy", index=self.index)
        log.warning(f"strategy[{self.index}]: HALTING - {reason}")
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(K.strategy_state(self.index), "HALTED")
        pipe.set(K.strategy_cooldown_reason(self.index), reason)
        pipe.execute()

    # ------------------------------------------------------------------
    # Signal emission
    # ------------------------------------------------------------------

    def _emit_signal(
        self,
        *,
        intent: SignalIntent,
        side: OptionSide,
        strike: int,
        instrument_token: str,
        diff_at_signal: float,
        sum_ce: float,
        sum_pe: float,
        delta: float,
    ) -> str:
        sig_id = self._compute_sig_id(intent, side, strike)
        signal = Signal(
            sig_id=sig_id,
            index=self.index,  # type: ignore[arg-type]
            side=side,
            strike=strike,
            instrument_token=instrument_token,
            intent=intent,
            qty_lots=int(self.config.qty_lots),
            diff_at_signal=round(float(diff_at_signal), 4),
            sum_ce_at_signal=round(float(sum_ce), 4),
            sum_pe_at_signal=round(float(sum_pe), 4),
            delta_at_signal=round(float(delta), 4),
            delta_pcr_at_signal=None,
            strategy_version=self._strategy_version,
            ts=datetime.now(UTC),
        )
        payload = signal.model_dump(mode="json")
        payload_json = orjson.dumps(payload)
        sig_key = K.strategy_signal(sig_id)

        created = bool(self.redis.set(sig_key, payload_json, nx=True))
        if not created:
            return sig_id

        stream_fields: dict[str, str | int | float] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, str | int | float):
                stream_fields[key] = value
            else:
                stream_fields[key] = str(value)

        pipe = self.redis.pipeline(transaction=False)
        pipe.sadd(K.STRATEGY_SIGNALS_ACTIVE, sig_id)
        pipe.xadd(
            K.STRATEGY_STREAM_SIGNALS,
            cast(Any, stream_fields),
            maxlen=5_000,
            approximate=True,
        )
        pipe.incr(K.STRATEGY_SIGNALS_COUNTER)
        pipe.execute()
        return sig_id

    def _compute_sig_id(self, intent: SignalIntent, side: OptionSide, strike: int) -> str:
        raw = f"{self.index}|{self._tick_seq}|{side}|{strike}|{intent.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Redis I/O helpers
    # ------------------------------------------------------------------

    def _ensure_consumer_group(self) -> None:
        try:
            self.redis.xgroup_create(self._stream, self._group, id="$", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                logger.warning(f"xgroup_create raised: {exc!r}")

    def _read_str(self, key: str) -> str:
        return _decode(self.redis.get(key))

    def _read_int(self, key: str) -> int:
        raw = self._read_str(key)
        try:
            return int(raw) if raw else 0
        except ValueError:
            return 0

    def _read_float(self, key: str) -> float:
        raw = self._read_str(key)
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    def _read_basket(self) -> dict[str, list[str]]:
        raw = self.redis.get(K.strategy_basket(self.index))
        if not raw:
            return {"ce": [], "pe": []}
        parsed = _loads(raw)
        if not isinstance(parsed, dict):
            return {"ce": [], "pe": []}
        ce_raw = parsed.get("ce")
        pe_raw = parsed.get("pe")
        ce = ce_raw if isinstance(ce_raw, list) else []
        pe = pe_raw if isinstance(pe_raw, list) else []
        return {"ce": [str(t) for t in ce], "pe": [str(t) for t in pe]}

    def _read_pre_open_premiums(self, tokens: list[str]) -> dict[str, float]:
        raw = self.redis.get(K.strategy_pre_open(self.index))
        if not raw:
            return {}
        snap = _loads(raw)
        if not isinstance(snap, dict):
            return {}
        out: dict[str, float] = {}
        for token in tokens:
            leaf = snap.get(token)
            if not isinstance(leaf, dict):
                continue
            value = leaf.get("ltp", leaf.get("pre_open_premium", 0.0))
            out[token] = float(value or 0.0)
        return out

    def _read_current_premiums(self, tokens: list[str]) -> dict[str, float]:
        chain = self._read_option_chain()
        wanted = set(tokens)
        out: dict[str, float] = {}
        for sides in chain.values():
            if not isinstance(sides, dict):
                continue
            for side in ("ce", "pe"):
                leaf = sides.get(side)
                if not isinstance(leaf, dict):
                    continue
                token = leaf.get("token")
                if token in wanted:
                    out[str(token)] = float(leaf.get("ltp") or 0.0)
        return out

    def _read_leaf(self, token: str) -> dict[str, Any] | None:
        chain = self._read_option_chain()
        for sides in chain.values():
            if not isinstance(sides, dict):
                continue
            for side in ("ce", "pe"):
                leaf = sides.get(side)
                if isinstance(leaf, dict) and leaf.get("token") == token:
                    return dict(leaf)
        return None

    def _read_option_chain(self) -> dict[str, Any]:
        raw = self.redis.get(K.market_data_index_option_chain(self.index))
        if not raw:
            return {}
        parsed = _loads(raw)
        return parsed if isinstance(parsed, dict) else {}

    def _read_system_snapshot(self) -> dict[str, Any]:
        pipe = self.redis.pipeline(transaction=False)
        pipe.get(K.SYSTEM_FLAGS_READY)
        pipe.get(K.SYSTEM_FLAGS_TRADING_ACTIVE)
        pipe.get(K.SYSTEM_FLAGS_DAILY_LOSS_CIRCUIT_TRIGGERED)
        ready, trading_active, daily_loss = pipe.execute()
        return {
            "ready": _decode(ready),
            "trading_active": _decode(trading_active),
            "daily_loss_circuit_triggered": _decode(daily_loss),
            "kill_switch_engaged_nse_fo": False,
        }

    def _read_spread_skip_pct(self) -> float:
        raw = self.redis.get(K.STRATEGY_CONFIGS_EXECUTION)
        if not raw:
            return 0.05
        parsed = _loads(raw)
        if not isinstance(parsed, dict):
            return 0.05
        try:
            return float(parsed.get("spread_skip_pct") or 0.05)
        except (TypeError, ValueError):
            return 0.05

    def _strike_for_token(self, token: str, leaf: dict[str, Any]) -> int:
        chain = self._read_option_chain()
        for strike_str, sides in chain.items():
            if not isinstance(sides, dict):
                continue
            for side in ("ce", "pe"):
                cell = sides.get(side)
                if isinstance(cell, dict) and cell.get("token") == token:
                    try:
                        return int(strike_str)
                    except (TypeError, ValueError):
                        break
        return int(leaf.get("strike") or 0)

    def _set_state(self, new_state: StrategyState) -> None:
        self.redis.set(K.strategy_state(self.index), new_state)

    def _get_state(self) -> StrategyState:
        raw = self._read_str(K.strategy_state(self.index)) or "FLAT"
        if raw in _VALID_STATES:
            return raw  # type: ignore[return-value]
        return "FLAT"

    def _is_index_enabled(self) -> bool:
        return self._read_str(K.strategy_enabled(self.index)).lower() == "true"

    def _incr_counter(self, key: str) -> None:
        self.redis.incr(key)

    def _persist_live_view(
        self,
        diffs: dict[str, float],
        sum_ce: float,
        sum_pe: float,
        delta: float,
    ) -> None:
        ts_ms = int(time.time() * 1000)
        state = self._get_state()
        view = StrategyView(
            index=self.index,  # type: ignore[arg-type]
            enabled=self._is_index_enabled(),
            state=state,
            sum_ce=float(sum_ce),
            sum_pe=float(sum_pe),
            delta=float(delta),
            diffs=diffs,
            cooldown_until_ts=self._read_int(K.strategy_cooldown_until_ts(self.index)) or None,
            cooldown_reason=self._read_str(K.strategy_cooldown_reason(self.index)) or None,
            entries_today=self._read_int(K.strategy_counters_entries_today(self.index)),
            reversals_today=self._read_int(K.strategy_counters_reversals_today(self.index)),
            wins_today=self._read_int(K.strategy_counters_wins_today(self.index)),
            last_decision_ts=ts_ms,
            ts=datetime.now(UTC),
        )

        pipe = self.redis.pipeline(transaction=False)
        pipe.set(K.strategy_live_sum_ce(self.index), f"{sum_ce:.4f}")
        pipe.set(K.strategy_live_sum_pe(self.index), f"{sum_pe:.4f}")
        pipe.set(K.strategy_live_delta(self.index), f"{delta:.4f}")
        pipe.set(K.strategy_live_diffs(self.index), orjson.dumps(diffs))
        pipe.set(K.strategy_live_last_decision_ts(self.index), str(ts_ms))
        pipe.set(K.ui_view_strategy(self.index), orjson.dumps(view.model_dump(mode="json")))
        pipe.publish(K.UI_PUB_VIEW, K.ui_view_strategy(self.index))
        pipe.execute()

    def _heartbeat(self) -> None:
        self.redis.hset(
            K.SYSTEM_HEALTH_HEARTBEATS,
            f"strategy:{self.index}",
            str(int(time.time() * 1000)),
        )

    def _mark_exited(self) -> None:
        self.redis.set(K.system_flag_engine_exited(f"strategy:{self.index}"), "true")
