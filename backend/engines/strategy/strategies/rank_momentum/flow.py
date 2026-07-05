"""Order-flow math for the Final Foolproof Rank Momentum Specification v2.

Implements the "Conditions and Formula Set" verbatim (numbers reference the
formula table):

    1  ND   = CEΔ − PEΔ                (executed option premium notional)
    2  DV   = ΔND / Δt
    3  DA   = ΔDV / Δt
    4  RND  = ND / total volume notional
    6  RV   = ΔRank / Δt               (positive = improving toward rank 1)
    8  GS   = ND(above) − ND(current)
    9  ETA  = GS / DV
    10 RST  = time at rank / market time
    11 FS   = |ΔND| / Δt (normalized)
    12 CSS  = 0.5·DV + 0.3·Volume + 0.2·Price       (normalized 0..1)
    13 MS   = 0.35·ND + 0.25·DV + 0.20·RV + 0.20·RST (normalized 0..1)
    14 CS   = 0.30·MS + 0.25·RST + 0.20·CSS + 0.15·OS + 0.10·VolQ
    17 ES   = 0.40·FlipOff + 0.30·RankLoss + 0.20·DVReversal + 0.10·VWAPLoss
    18 DRS  = 0.30·ND + 0.25·DV + 0.15·RV + 0.15·RST + 0.10·CSS + 0.05·FS

Normalization: each raw component is squashed to 0..1 against a config
scale (`x / (x + scale)` for magnitudes, sign preserved where directional)
so the weighted scores stay in 0..1 and the CS >= 0.85 gate is meaningful.
All state is in-memory rolling windows — every incoming tick recalculates;
nothing waits for candle closes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _squash(x: float, scale: float) -> float:
    """|x| -> 0..1 with soft saturation at `scale`."""
    ax = abs(x)
    return ax / (ax + scale) if scale > 0 else 0.0


@dataclass(slots=True)
class SymbolFlow:
    """Per-symbol rolling order-flow state (rings pruned to ~3 minutes)."""

    first_ce: float | None = None  # session-start CE premium notional
    first_pe: float | None = None
    nd_ring: list[tuple[int, float]] = field(default_factory=list)  # (ts_ms, ND)
    dv_prev: float = 0.0
    spot_sum: float = 0.0  # running mean spot ~ session VWAP proxy
    spot_n: int = 0

    nd: float = 0.0
    rnd: float = 0.0
    dv: float = 0.0  # per second, 1m window
    dv_30s: float = 0.0
    da: float = 0.0
    fs_raw: float = 0.0
    spot: float = 0.0
    spot_mean: float = 0.0

    def update(self, now_ms: int, ce_notional: float, pe_notional: float, spot: float) -> None:
        if self.first_ce is None:
            self.first_ce, self.first_pe = ce_notional, pe_notional
        ce_d = ce_notional - self.first_ce
        pe_d = pe_notional - float(self.first_pe or 0.0)
        self.nd = ce_d - pe_d  # (1) Net Delta
        total = abs(ce_d) + abs(pe_d)
        self.rnd = self.nd / total if total > 0 else 0.0  # (4)

        self.nd_ring.append((now_ms, self.nd))
        cutoff = now_ms - 200_000
        while self.nd_ring and self.nd_ring[0][0] < cutoff:
            self.nd_ring.pop(0)

        def _dv(window_ms: int) -> float:
            base = None
            for ts, nd in self.nd_ring:
                if ts <= now_ms - window_ms:
                    base = (ts, nd)
                else:
                    break
            if base is None:
                base = self.nd_ring[0]
            dt = (now_ms - base[0]) / 1000.0
            return (self.nd - base[1]) / dt if dt > 0 else 0.0

        dv_1m = _dv(60_000)  # (2) Delta Velocity
        self.dv_30s = _dv(30_000)
        self.da = dv_1m - self.dv_prev  # (3) Delta Acceleration
        self.fs_raw = abs(dv_1m)  # (11) Flip-Off raw magnitude
        self.dv_prev = dv_1m
        self.dv = dv_1m

        if spot > 0:
            self.spot = spot
            self.spot_sum += spot
            self.spot_n += 1
            self.spot_mean = self.spot_sum / self.spot_n


@dataclass(slots=True)
class RankState:
    rank: int = 0
    prev_rank: int = 0
    rank_since_ms: int = 0
    overtakes: int = 0
    session_start_ms: int = 0

    def apply(self, new_rank: int, now_ms: int) -> None:
        if self.session_start_ms == 0:
            self.session_start_ms = now_ms
        if new_rank != self.rank:
            self.prev_rank = self.rank or new_rank
            if self.rank and new_rank < self.rank:
                self.overtakes += 1
            self.rank = new_rank
            self.rank_since_ms = now_ms
        elif self.rank_since_ms == 0:
            self.rank = new_rank
            self.rank_since_ms = now_ms

    def rv(self) -> float:
        """(6) Rank velocity: positions gained per minute (positive = up)."""
        return float(self.prev_rank - self.rank) if self.rank else 0.0

    def rst(self, now_ms: int) -> float:
        """(10) Rank stability: time at current rank / session time, 0..1."""
        session = max(1, now_ms - self.session_start_ms)
        return min(1.0, (now_ms - self.rank_since_ms) / session)


def scores(
    flow: SymbolFlow,
    rank: RankState,
    now_ms: int,
    cfg: dict[str, Any],
) -> dict[str, float]:
    """All normalized composite scores for one symbol (formulas 12-14, 17-18)."""
    nd_scale = float(cfg.get("nd_scale", 5_000_000))
    dv_scale = float(cfg.get("dv_scale", 50_000))
    nd_n = _squash(flow.nd, nd_scale)
    dv_n = _squash(flow.dv, dv_scale)
    rv = rank.rv()
    rv_n = _squash(rv, 3.0)
    rst = rank.rst(now_ms)
    fs_n = _squash(flow.fs_raw, dv_scale * 2)
    vol_spike = _squash(abs(flow.dv_30s), dv_scale)
    price_dev = _squash(
        (flow.spot - flow.spot_mean) / flow.spot_mean if flow.spot_mean > 0 else 0.0, 0.005
    )
    css = 0.5 * dv_n + 0.3 * vol_spike + 0.2 * price_dev  # (12)
    ms = 0.35 * nd_n + 0.25 * dv_n + 0.20 * rv_n + 0.20 * rst  # (13)
    os_n = _squash(rv, 5.0)  # (7) overtake score, normalized
    vol_quality = _squash(abs(flow.rnd), 0.5)
    cs = 0.30 * ms + 0.25 * rst + 0.20 * css + 0.15 * os_n + 0.10 * vol_quality  # (14)
    drs = 0.30 * nd_n + 0.25 * dv_n + 0.15 * rv_n + 0.15 * rst + 0.10 * css + 0.05 * fs_n  # (18)
    return {
        "nd": flow.nd,
        "rnd": flow.rnd,
        "dv": flow.dv,
        "da": flow.da,
        "rv": rv,
        "rst": round(rst, 4),
        "css": round(css, 4),
        "ms": round(ms, 4),
        "cs": round(cs, 4),
        "drs": round(drs, 4),
    }


def exit_score(
    *,
    side: str,
    flow: SymbolFlow,
    rank: RankState,
    rank_at_entry: int,
    cfg: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """(17) ES = 0.40·FlipOff + 0.30·RankLoss + 0.20·DVReversal + 0.10·VWAPLoss.

    Directional: for a CE leg, bearish flow is adverse; mirrored for PE.
    """
    sign = 1.0 if side == "CE" else -1.0
    dv_scale = float(cfg.get("dv_scale", 50_000))
    # Flip-off: adverse ND velocity (flow turning against the leg)
    adverse_dv = max(0.0, -sign * flow.dv)
    flip = _squash(adverse_dv, dv_scale)
    # Rank loss: positions dropped since entry
    rank_loss = _squash(max(0, rank.rank - rank_at_entry), 3.0)
    # DV reversal: acceleration against the leg
    dv_rev = _squash(max(0.0, -sign * flow.da), dv_scale)
    # VWAP loss: spot on the wrong side of the session mean
    dev = (flow.spot - flow.spot_mean) / flow.spot_mean if flow.spot_mean > 0 else 0.0
    vwap_loss = _squash(max(0.0, -sign * dev), 0.003)
    es = 0.40 * flip + 0.30 * rank_loss + 0.20 * dv_rev + 0.10 * vwap_loss
    return round(es, 4), {
        "flip": round(flip, 4),
        "rank_loss": round(rank_loss, 4),
        "dv_reversal": round(dv_rev, 4),
        "vwap_loss": round(vwap_loss, 4),
    }
