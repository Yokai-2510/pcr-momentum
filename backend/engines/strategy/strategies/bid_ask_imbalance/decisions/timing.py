"""Time-of-day windowing (Strategy.md §6).

The minimum quality score required for entry varies by intraday phase:

    09:15 - 09:30   OPENING                min_score 8   (highest noise)
    09:30 - 11:30   PRIMARY                min_score 6
    11:30 - 13:30   MID                    min_score 7   (lunch low-liquidity)
    13:30 - 15:00   CONTINUATION_ONLY      min_score 7   (no fresh entries)
    15:00 - 15:30   EXIT_ONLY              entries blocked entirely

Configurable via `strategy:configs:strategies:{sid}.time_windows`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time
from typing import Any


@dataclass(slots=True, frozen=True)
class TimingWindow:
    start: dt_time
    end: dt_time
    phase: str
    min_score: int | None  # None means entries blocked


def parse_windows(config_windows: list[dict[str, Any]] | None) -> list[TimingWindow]:
    """Parse the `time_windows` config blob into typed windows.

    Returns empty list if config is missing/malformed; caller should treat
    that as "no entries allowed" (safe default).
    """
    if not config_windows:
        return []

    out: list[TimingWindow] = []
    for w in config_windows:
        try:
            start_h, start_m = (int(x) for x in w["start"].split(":"))
            end_h, end_m = (int(x) for x in w["end"].split(":"))
            min_score = w.get("min_score")
            min_score_int = int(min_score) if min_score is not None else None
            out.append(
                TimingWindow(
                    start=dt_time(start_h, start_m),
                    end=dt_time(end_h, end_m),
                    phase=str(w.get("phase", "UNKNOWN")),
                    min_score=min_score_int,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def current_window(now_t: dt_time, windows: list[TimingWindow]) -> TimingWindow | None:
    """Return the timing window containing `now_t` (IST), or None if outside."""
    for w in windows:
        if w.start <= now_t < w.end:
            return w
    return None


def entry_allowed(
    now_t: dt_time, windows: list[TimingWindow], score: int
) -> tuple[bool, str]:
    """Is a fresh entry allowed at `now_t` with the given quality score?"""
    w = current_window(now_t, windows)
    if w is None:
        return False, "outside_trading_window"
    if w.min_score is None:
        return False, f"phase_{w.phase}_blocks_entries"
    if score < w.min_score:
        return False, f"score_{score}_below_phase_{w.phase}_min_{w.min_score}"
    return True, ""


def is_continuation_phase(now_t: dt_time, windows: list[TimingWindow]) -> bool:
    """During CONTINUATION_ONLY phase, only entries on the same side as a
    recent exit are allowed (Strategy.md §6).
    """
    w = current_window(now_t, windows)
    return bool(w and w.phase == "CONTINUATION_ONLY")


def is_exit_only_phase(now_t: dt_time, windows: list[TimingWindow]) -> bool:
    w = current_window(now_t, windows)
    return bool(w and w.phase == "EXIT_ONLY")
