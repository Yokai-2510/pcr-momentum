"""Tick speed — consecutive direction streak (Strategy.md §4.5).

    consecutive_upticks = max k such that LTP[-1] > LTP[-2] > ... > LTP[-k]
                                       AND  ts[-1] - ts[-k] <= window_ms

Strong momentum = consecutive_upticks >= min_consecutive (default 3) within
window_ms (default 1000ms).

Symmetric for downticks. Single-tick spikes are unreliable; this filter is
the noise floor for the LTP-position component of the quality score.
"""

from __future__ import annotations

from engines.strategy.strategies.bid_ask_imbalance.buffer import StrikeBuffer


def consecutive_upticks(buffer: StrikeBuffer, *, window_ms: int = 1000) -> int:
    """Count consecutive strictly-increasing LTPs ending at the most recent
    observation, within `window_ms` of that observation.
    """
    obs_list = list(reversed(buffer.last_n(50)))  # newest-first; cap is implicit
    if not obs_list:
        return 0

    head = obs_list[0]
    if head.ltp is None:
        return 0

    streak = 1
    cutoff = head.ts - window_ms
    last_ltp = head.ltp

    for obs in obs_list[1:]:
        if obs.ltp is None or obs.ts < cutoff:
            break
        if obs.ltp < last_ltp:
            last_ltp = obs.ltp
            streak += 1
        else:
            break
    # streak counts strictly increasing from the past forward; if we walked 1
    # observation we have streak=1 (just the latest). Caller compares against
    # `min_consecutive`.
    return streak


def consecutive_downticks(buffer: StrikeBuffer, *, window_ms: int = 1000) -> int:
    """Count consecutive strictly-decreasing LTPs ending at the most recent
    observation, within `window_ms` of that observation.
    """
    obs_list = list(reversed(buffer.last_n(buffer._dq.maxlen or 50)))
    if not obs_list:
        return 0

    head = obs_list[0]
    if head.ltp is None:
        return 0

    streak = 1
    cutoff = head.ts - window_ms
    last_ltp = head.ltp

    for obs in obs_list[1:]:
        if obs.ltp is None or obs.ts < cutoff:
            break
        if obs.ltp > last_ltp:
            last_ltp = obs.ltp
            streak += 1
        else:
            break
    return streak


def has_strong_up(buffer: StrikeBuffer, *, min_consecutive: int = 3, window_ms: int = 1000) -> bool:
    return consecutive_upticks(buffer, window_ms=window_ms) >= min_consecutive


def has_strong_down(
    buffer: StrikeBuffer, *, min_consecutive: int = 3, window_ms: int = 1000
) -> bool:
    return consecutive_downticks(buffer, window_ms=window_ms) >= min_consecutive
