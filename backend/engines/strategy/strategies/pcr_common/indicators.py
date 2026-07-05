"""Pure indicator/state-machine ports from pcr_analytics (tick-driven).

Every function here recomputes INSTANTLY from the live chain view — no
recorded timestamps, no fixed logging cadence. State machines emit a signal
only on the first directional reading of the session and on genuine flips,
exactly matching the original engine's crossover semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Leaf = dict[str, Any]
ChainMap = dict[int, Leaf]  # strike -> leaf {ltp, vol, oi, ts, token, ...}


def band_strikes(atm: int, step: int, n: int) -> list[int]:
    return [atm + i * step for i in range(-n, n + 1)]


def band_totals(ce: ChainMap, pe: ChainMap, atm: int, step: int, n: int) -> dict[str, float]:
    """ATM±n band totals. Volume band per the original Volume tab:
    CE volume = ATM..ATM+n calls; PE volume = ATM-n..ATM puts.
    OI totals use the full symmetric band on each side."""
    full = band_strikes(atm, step, n)
    ce_up = [atm + i * step for i in range(0, n + 1)]
    pe_dn = [atm - i * step for i in range(0, n + 1)]

    def _sum(m: ChainMap, ss: list[int], col: str) -> float:
        return float(sum(float((m.get(s) or {}).get(col) or 0) for s in ss))

    return {
        "ce_oi": _sum(ce, full, "oi"),
        "pe_oi": _sum(pe, full, "oi"),
        "ce_vol": _sum(ce, ce_up, "vol"),
        "pe_vol": _sum(pe, pe_dn, "vol"),
    }


@dataclass(slots=True)
class FlipState:
    """Sign state machine: emit on first directional reading + each flip."""

    prev: str | None = None

    def update(self, value: float) -> str | None:
        state = "BUY" if value < 0 else "SELL" if value > 0 else None
        if state is not None and state != self.prev:
            self.prev = state
            return state
        return None


@dataclass(slots=True)
class OiDiffState:
    """OI crossover per the original data_engine: diff = PE_cumm - CE_cumm.

    Seed: first NON-ZERO diff after the first valid tick -> diff>0 BUY (CE),
    diff<0 SELL (PE). Then flip only on a sign crossover vs prev diff."""

    first_ce: float | None = None
    first_pe: float | None = None
    prev_diff: float | None = None
    position: str | None = None  # last emitted side (BUY/SELL)

    def update(self, ce_oi: float, pe_oi: float) -> tuple[str | None, float | None]:
        if self.first_ce is None:
            self.first_ce, self.first_pe = ce_oi, pe_oi
            return None, None  # first tick — no diff yet (original behavior)
        diff = (pe_oi - float(self.first_pe or 0)) - (ce_oi - self.first_ce)
        prev = self.prev_diff
        self.prev_diff = diff
        signal: str | None = None
        if self.position is None:
            if diff > 0:
                signal = "BUY"
            elif diff < 0:
                signal = "SELL"
        elif prev is not None:
            if self.position == "SELL" and prev <= 0 and diff > 0:
                signal = "BUY"
            elif self.position == "BUY" and prev >= 0 and diff < 0:
                signal = "SELL"
        if signal:
            self.position = signal
        return signal, diff


@dataclass(slots=True)
class VwapState:
    """Session VWAP from spot x incremental option volume; 0.05% band.

    Emits only fresh directional crossovers; neutral never resets the side."""

    band_pct: float = 0.0005
    cum_pv: float = 0.0
    cum_vol: float = 0.0
    prev_ce: float | None = None
    prev_pe: float | None = None
    prev_dir: str | None = None
    vwap: float = 0.0

    def update(self, spot: float, ce_cum_vol: float, pe_cum_vol: float) -> str | None:
        ce_d = max(0.0, ce_cum_vol - self.prev_ce) if self.prev_ce is not None else ce_cum_vol
        pe_d = max(0.0, pe_cum_vol - self.prev_pe) if self.prev_pe is not None else pe_cum_vol
        self.prev_ce, self.prev_pe = ce_cum_vol, pe_cum_vol
        tick_vol = ce_d + pe_d
        if spot > 0 and tick_vol > 0:
            self.cum_pv += spot * tick_vol
            self.cum_vol += tick_vol
        if self.cum_vol <= 0 or spot <= 0:
            return None
        self.vwap = self.cum_pv / self.cum_vol
        band = self.vwap * self.band_pct
        sig = "BUY" if spot > self.vwap + band else "SELL" if spot < self.vwap - band else None
        if sig is not None and sig != self.prev_dir:
            self.prev_dir = sig
            return sig
        return None


@dataclass(slots=True)
class LtpStrengthState:
    """Dr. Vijay's LTP option-strength (STEP 11-13), tick-driven.

    CE_SUM = sum of (ce_ltp now - session first) over ATM + 3 LOWER strikes;
    PE_SUM over ATM + 3 HIGHER strikes; Dir = CE_SUM - PE_SUM; Rolling = the
    ~5-minute change of the same sums. Strict BUY/SELL needs all four PLUS
    spot vs session VWAP. Regime flips only vs the last side traded."""

    first: dict[int, tuple[float, float]] = field(default_factory=dict)
    ring: list[tuple[int, dict[int, tuple[float, float]]]] = field(default_factory=list)
    regime: str | None = None
    rolling_ms: int = 300_000

    def update(
        self,
        now_ms: int,
        atm: int,
        step: int,
        ce: ChainMap,
        pe: ChainMap,
        spot: float,
        vwap: float,
    ) -> tuple[str | None, dict[str, float]]:
        ce_strikes = [atm - k * step for k in range(4)]
        pe_strikes = [atm + k * step for k in range(4)]
        cur: dict[int, tuple[float, float]] = {}
        for s in set(ce_strikes + pe_strikes):
            cur[s] = (
                float((ce.get(s) or {}).get("ltp") or 0),
                float((pe.get(s) or {}).get("ltp") or 0),
            )
        for s, v in cur.items():
            if s not in self.first and (v[0] > 0 or v[1] > 0):
                self.first[s] = v
        self.ring.append((now_ms, cur))
        cutoff = now_ms - self.rolling_ms - 60_000
        while self.ring and self.ring[0][0] < cutoff:
            self.ring.pop(0)
        ago: dict[int, tuple[float, float]] = {}
        for ts, snap in self.ring:
            if ts <= now_ms - self.rolling_ms:
                ago = snap
            else:
                break

        def _sess(strikes: list[int], idx: int) -> float:
            tot = 0.0
            for s in strikes:
                c, f = cur.get(s), self.first.get(s)
                if c and f and c[idx] > 0 and f[idx] > 0:
                    tot += c[idx] - f[idx]
            return tot

        def _roll(strikes: list[int], idx: int) -> float:
            if not ago:
                return 0.0
            tot = 0.0
            for s in strikes:
                c, a = cur.get(s), ago.get(s)
                if c and a and c[idx] > 0 and a[idx] > 0:
                    tot += c[idx] - a[idx]
            return tot

        ce_sum = _sess(ce_strikes, 0)
        pe_sum = _sess(pe_strikes, 1)
        dir_s = ce_sum - pe_sum
        roll_s = _roll(ce_strikes, 0) - _roll(pe_strikes, 1)
        metrics = {
            "ce_sum": round(ce_sum, 2),
            "pe_sum": round(pe_sum, 2),
            "directional_strength": round(dir_s, 2),
            "rolling_strength": round(roll_s, 2),
            "vwap": round(vwap, 2),
            "spot": spot,
        }
        sig: str | None = None
        if ce_sum > 0 and pe_sum < 0 and dir_s > 0 and roll_s > 0 and vwap > 0 and spot > vwap:
            sig = "BUY"
        elif ce_sum < 0 and pe_sum > 0 and dir_s < 0 and roll_s < 0 and vwap > 0 and spot < vwap:
            sig = "SELL"
        if sig is not None and sig != self.regime:
            self.regime = sig
            return sig, metrics
        return None, metrics
