"""
Per-strike rolling buffer.

Keeps the last N tick observations for each basket token in memory. Used by
metrics that need history across ticks:

    - tick_speed.py        consecutive upticks/downticks
    - reversal.py          imbalance drop over the last 2-3 ticks
    - ask_wall.py          wall sub-state (HOLDING vs ABSORBING vs REFRESHING)

Buffer is per-vessel (vessel-private memory). The size is configurable via
`strategy:configs:strategies:{sid}.buffer.ring_size` (default 50).

This module is in-memory only. Crash recovery is by design: on restart,
buffers start empty; metrics that need history will return None until enough
ticks accumulate (typically <1 second of trading).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class TickObservation:
    ts: int  # ms
    ltp: float | None
    best_bid: float | None
    best_ask: float | None
    best_bid_qty: int | None
    best_ask_qty: int | None
    total_bid_qty: int | None
    total_ask_qty: int | None
    imbalance: float | None       # cached from this tick's metric compute
    spread: float | None
    ask_wall_present: bool | None
    aggressor: str | None         # "buy" | "sell" | None


class StrikeBuffer:
    """A rolling window of TickObservation for one strike token."""

    def __init__(self, capacity: int = 50) -> None:
        self._dq: deque[TickObservation] = deque(maxlen=capacity)

    def push(self, obs: TickObservation) -> None:
        # Skip duplicate timestamps (same tick re-evaluated due to sibling
        # token waking the vessel) — preserves true "consecutive ticks"
        # semantics for tick_speed and reversal detection.
        if self._dq and obs.ts == self._dq[-1].ts:
            return
        self._dq.append(obs)

    def latest(self) -> TickObservation | None:
        return self._dq[-1] if self._dq else None

    def last_n(self, n: int) -> list[TickObservation]:
        if n <= 0 or not self._dq:
            return []
        return list(self._dq)[-n:]

    def __len__(self) -> int:
        return len(self._dq)


class BufferStore:
    """Per-vessel container of {token: StrikeBuffer}.

    Lifecycle: created at vessel BOOT, lives until vessel DRAIN. Tokens are
    added/removed dynamically as the basket shifts (Strategy.md §3.2).
    """

    def __init__(self, capacity: int = 50) -> None:
        self.capacity = capacity
        self._buffers: dict[str, StrikeBuffer] = {}

    def buffer_for(self, token: str) -> StrikeBuffer:
        buf = self._buffers.get(token)
        if buf is None:
            buf = StrikeBuffer(self.capacity)
            self._buffers[token] = buf
        return buf

    def discard(self, tokens: list[str]) -> None:
        """Drop buffers for tokens that fell out of the basket."""
        for t in tokens:
            self._buffers.pop(t, None)

    def known_tokens(self) -> list[str]:
        return list(self._buffers.keys())
