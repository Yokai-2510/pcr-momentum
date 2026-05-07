"""Vessel state machine (Strategy.md §5.5).

Per-vessel state lives in Redis under `strategy:{sid}:{idx}:state`. Cooldown
clock + counters are read from Redis on every evaluation — this is so a
restart picks up exactly where the vessel left off (paper or live).

Phase                State            Permitted actions
─────                ─────            ─────────────────
LIVE                 FLAT             Evaluate entry gates -> emit ENTRY
LIVE                 IN_CE / IN_PE    Evaluate continuation + reversal -> EXIT/FLIP
LIVE                 COOLDOWN         No entries; observe + write metrics
LIVE                 HALTED           No entries, no exits (drain script handles closure)

Transitions are driven by the runner based on Action returned by the strategy.
This module just provides typed helpers + Redis I/O for state mutations.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from state import keys as K

VesselState = Literal["FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"]
_VALID_STATES: set[str] = {"FLAT", "IN_CE", "IN_PE", "COOLDOWN", "HALTED"}


def read_state(redis_sync: Any, sid: str, index: str) -> VesselState:
    raw = redis_sync.get(K.vessel_state(sid, index))
    if isinstance(raw, bytes):
        raw = raw.decode()
    if raw in _VALID_STATES:
        return raw  # type: ignore[return-value]
    return "FLAT"


def set_state(redis_sync: Any, sid: str, index: str, new_state: VesselState) -> None:
    if new_state not in _VALID_STATES:
        raise ValueError(f"invalid vessel state {new_state!r}")
    redis_sync.set(K.vessel_state(sid, index), new_state)


def enter_cooldown(
    redis_sync: Any, sid: str, index: str, reason: str, duration_sec: int
) -> None:
    until_ms = int(time.time() * 1000) + duration_sec * 1000
    pipe = redis_sync.pipeline(transaction=False)
    pipe.set(K.vessel_state(sid, index), "COOLDOWN")
    pipe.set(K.vessel_cooldown_until_ts(sid, index), str(until_ms))
    pipe.set(K.vessel_cooldown_reason(sid, index), reason)
    pipe.execute()


def maybe_exit_cooldown(redis_sync: Any, sid: str, index: str) -> bool:
    """If cooldown timer has elapsed, transition to FLAT. Returns True if exited."""
    state = read_state(redis_sync, sid, index)
    if state != "COOLDOWN":
        return False
    until_raw = redis_sync.get(K.vessel_cooldown_until_ts(sid, index))
    try:
        until = int(until_raw) if until_raw else 0
    except (TypeError, ValueError):
        until = 0
    now_ms = int(time.time() * 1000)
    if until <= 0 or now_ms >= until:
        set_state(redis_sync, sid, index, "FLAT")
        return True
    return False


def halt(redis_sync: Any, sid: str, index: str, reason: str) -> None:
    pipe = redis_sync.pipeline(transaction=False)
    pipe.set(K.vessel_state(sid, index), "HALTED")
    pipe.set(K.vessel_cooldown_reason(sid, index), reason)
    pipe.execute()


def is_enabled(redis_sync: Any, sid: str, index: str) -> bool:
    raw = redis_sync.get(K.vessel_enabled(sid, index))
    if isinstance(raw, bytes):
        raw = raw.decode()
    return str(raw or "").lower() == "true"


def increment_counter(redis_sync: Any, key: str) -> int:
    return int(redis_sync.incr(key))


def read_counter(redis_sync: Any, key: str) -> int:
    raw = redis_sync.get(key)
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0
