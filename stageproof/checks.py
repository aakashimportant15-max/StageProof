"""Sensor health checks and context helpers (T018-T020; RULES §3.2, §3.4-§3.6,
§3.8-§3.10).

Pure functions over Windows and series: no verdicts, no clock, no I/O.
Window series (`stage`/`logq`) are NaN-aligned per tick and end at the
current tick; NaN marks a tick with no accepted value. This module imports
only domain, settings and numpy (ARCHITECTURE §3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

import numpy as np

from .domain import Support, Trend, Window
from .settings import (DriftThresholds, HealthThresholds, RainThresholds,
                       ReplayThresholds, TrendThresholds)

__all__ = [
    "HealthResult", "health_checks", "upstream_trend", "rain_support",
    "drift_measure", "is_notable", "ReplayArchive",
]

_SHAPE_FLAGS = ("DROPOUT", "RANGE", "SPIKE", "STUCK", "NOISE")


@dataclass(frozen=True)
class HealthResult:
    """Outcome of the RULES §3.2 checks for one role at one tick."""
    role: str
    flags: tuple[str, ...] = ()
    shape_ok: bool = True
    rate_exceeded: bool = False
    noise_too_clean: bool = False
    detail: dict = field(default_factory=dict)


def _trailing_nan(series: np.ndarray) -> int:
    n = 0
    for v in series[::-1]:
        if math.isfinite(float(v)):
            break
        n += 1
    return n


def _diffs(series: np.ndarray, count: int) -> np.ndarray:
    """First differences of adjacent finite pairs, at most `count`."""
    out: list[float] = []
    for i in range(len(series) - 1, 0, -1):
        a, b = float(series[i - 1]), float(series[i])
        if math.isfinite(a) and math.isfinite(b):
            out.append(b - a)
        if len(out) >= count:
            break
    return np.asarray(out[::-1], dtype=float)


def health_checks(role: str, window: Window, cfg: HealthThresholds,
                  station_params: dict, sensor_min: float, sensor_max: float,
                  pred_change: Optional[float] = None) -> HealthResult:
    """RULES §3.2 shape plausibility for `role` at the current tick.

    `pred_change` = |log Q prediction change over the last `stuck_ticks`
    window| (used only by the B-only STUCK check). RATE_EXCEEDED and
    NOISE_TOO_CLEAN are informational and never set shape_ok = False (N-10).
    """
    series = np.asarray(window.stage.get(role) or (), dtype=float)
    flags: list[str] = []
    detail: dict = {}
    rate_exceeded = False
    noise_too_clean = False
    quant_step = max(float(station_params.get("quant_step", 0.01)), 1e-9)
    spike_max = float(station_params.get("spike_delta_max", quant_step * 10))

    if series.size == 0:
        return HealthResult(role=role, flags=("DROPOUT",), shape_ok=False,
                            detail={"history": 0})

    # DROPOUT: no accepted value for >= dropout_ticks consecutive ticks.
    if _trailing_nan(series) >= cfg.dropout_ticks:
        flags.append("DROPOUT")

    current = float(series[-1])

    # RANGE: stage outside the reach envelope, or non-finite-with-data.
    if math.isfinite(current):
        if current < sensor_min or current > sensor_max:
            flags.append("RANGE")
            detail["range"] = [sensor_min, sensor_max, current]
    elif not bool((window.missing or {}).get(role, False)):
        flags.append("RANGE")
        detail["range"] = [sensor_min, sensor_max, current]

    # SPIKE (retroactive on revert) and RATE_EXCEEDED (info, N-10).
    if math.isfinite(spike_max) and spike_max > 0 and series.size >= 2:
        scan_start = max(1, series.size - (cfg.spike_revert_ticks + 1))
        reverts: set[int] = set()
        for i in range(scan_start, series.size):
            if i in reverts:
                continue
            prev, cur = float(series[i - 1]), float(series[i])
            if not (math.isfinite(prev) and math.isfinite(cur)):
                continue
            if abs(cur - prev) <= spike_max:
                continue
            reverted = False
            for j in range(i + 1,
                           min(i + cfg.spike_revert_ticks,
                               series.size - 1) + 1):
                v = float(series[j])
                if math.isfinite(v) and \
                        abs(v - prev) <= cfg.spike_revert_tol_steps * quant_step:
                    reverted = True
                    reverts.add(j)
                    if j == series.size - 1:
                        flags.append("SPIKE")
                        detail["spike"] = [i, cur, prev]
                    break
            if not reverted and i == series.size - 1:
                rate_exceeded = True
                detail["rate"] = [cur - prev, spike_max]

    if role == "B":
        # STUCK: flat range while the model says flow is moving (B only).
        win = series[-cfg.stuck_ticks:]
        if win.size == cfg.stuck_ticks and np.all(np.isfinite(win)):
            rng = float(np.max(win) - np.min(win))
            if rng <= quant_step and pred_change is not None and \
                    pred_change >= cfg.stuck_pred_change_min:
                flags.append("STUCK")
                detail["stuck"] = [rng, pred_change]

        # NOISE: diff std vs station baseline (B only).
        baseline = max(float(station_params.get("noise_baseline_std", 0.0)),
                       1e-9)
        d = _diffs(series, cfg.noise_window_ticks)
        if d.size >= 3:
            ratio = float(np.std(d)) / baseline
            detail["noise_ratio"] = round(ratio, 3)
            if ratio > cfg.noise_ratio_max:
                flags.append("NOISE")

        # NOISE_TOO_CLEAN (P1 booster): second-diff std near zero while the
        # stage still moved — the fabricated-ramp signature.
        win = series[-(cfg.clean_window_ticks + 2):]
        d2: list[float] = []
        for i in range(2, win.size):
            a, b, c = float(win[i - 2]), float(win[i - 1]), float(win[i])
            if math.isfinite(a) and math.isfinite(b) and math.isfinite(c):
                d2.append(c - 2.0 * b + a)
        if len(d2) >= 3:
            move = np.ptp(win[np.isfinite(win)])
            if float(np.std(d2)) < cfg.clean_ratio_min * baseline and \
                    float(move) >= cfg.clean_min_move_steps * quant_step:
                noise_too_clean = True
                detail["clean_ratio"] = round(
                    float(np.std(d2)) / baseline, 4)

    shape_flags = [f for f in flags if f in _SHAPE_FLAGS]
    return HealthResult(role=role, flags=tuple(flags),
                        shape_ok=not shape_flags,
                        rate_exceeded=rate_exceeded,
                        noise_too_clean=noise_too_clean, detail=detail)


# ---------------------------------------------------------------------------
# T019 — trend, rain support, drift, notable
# ---------------------------------------------------------------------------

def upstream_trend(window: Window, cfg: TrendThresholds,
                   trust: Optional[dict] = None) -> Trend:
    """RULES §3.4: ln Q change over `up_window_ticks` from A (else T), only
    if that sensor is TRUSTED; RISING -> FALLING -> FLAT in that order."""
    trust = window.trust if trust is None else trust
    for role in ("A", "T"):
        series = window.logq.get(role) or ()
        if len(series) <= cfg.up_window_ticks:
            continue
        if not bool((trust or {}).get(role, False)):
            continue
        new = float(series[-1])
        old = float(series[-1 - cfg.up_window_ticks])
        if not (math.isfinite(new) and math.isfinite(old)):
            continue
        delta = new - old
        if delta >= cfg.up_rising_min:
            return Trend.RISING
        if delta <= -cfg.up_flat_max:
            return Trend.FALLING
        return Trend.FLAT
    return Trend.UNKNOWN


def rain_support(window: Window, cfg: RainThresholds,
                 yes_mm: Optional[float], no_mm: Optional[float]) -> Support:
    """RULES §3.6: accumulation over completed hours; UNKNOWN between
    thresholds, when the feed is stale/missing, or when thresholds are
    unresolved. No-lookahead alignment is the Window builder's duty."""
    if yes_mm is None or no_mm is None:
        return Support.UNKNOWN
    if not window.rain_available:
        return Support.UNKNOWN
    hourly = np.asarray(window.rain_hourly, dtype=float)
    if hourly.size < cfg.window_hours:
        return Support.UNKNOWN
    win = hourly[-cfg.window_hours:]
    finite = win[np.isfinite(win)]
    if finite.size == 0:
        return Support.UNKNOWN
    stale = 0
    for v in win[::-1]:
        if math.isfinite(float(v)):
            break
        stale += 1
    if stale > cfg.stale_hours:
        return Support.UNKNOWN
    acc = float(np.mean(finite)) * cfg.window_hours
    if acc >= yes_mm:
        return Support.YES
    if acc < no_mm:
        return Support.NO
    return Support.UNKNOWN


def drift_measure(residuals: Sequence[Optional[float]], tick_minutes: int,
                  cfg: DriftThresholds, window_ticks: int) -> bool:
    """RULES §3.9 body: |mean residual| < max_dev AND residual rate of change
    < max_rate_per_hour. The caller applies it only when the context is
    PHANTOM or SUPPRESSED."""
    vals = [float(r) for r in residuals[-window_ticks:]
            if r is not None and math.isfinite(float(r))]
    if len(vals) < 2:
        return False
    mean_dev = abs(float(np.mean(vals)))
    if mean_dev >= cfg.max_dev:
        return False
    half = max(1, len(vals) // 2)
    early = float(np.mean(vals[:half]))
    late = float(np.mean(vals[len(vals) - half:]))
    hours = max(len(vals) * tick_minutes / 60.0, 1e-9)
    rate = abs(late - early) / hours
    return rate < cfg.max_rate_per_hour


def is_notable(obs_stage: Optional[float], pred_stage: Optional[float],
               watch_stage: float) -> bool:
    """RULES §3.10."""
    if obs_stage is not None and math.isfinite(obs_stage) and \
            obs_stage >= watch_stage:
        return True
    if pred_stage is not None and math.isfinite(pred_stage) and \
            pred_stage >= watch_stage:
        return True
    return False


# ---------------------------------------------------------------------------
# T020 — replay match (P1)
# ---------------------------------------------------------------------------

class ReplayArchive:
    """Verbatim-replay detector (RULES §3.8).

    Precompute candidate archive windows (length `window_ticks`) from B's
    historical stage, keeping only windows that end at least `min_age_days`
    before the live feed starts. `match()` compares the last `window_ticks`
    accepted B values against every archive window, verbatim within
    `tol_steps` quantization steps, requiring the minimum stage range.
    """

    def __init__(self, cfg: ReplayThresholds, quant_step: float):
        self.cfg = cfg
        self.quant_step = max(float(quant_step), 1e-9)
        self._windows: list[np.ndarray] = []

    def add_history(self, stage: Sequence[Optional[float]],
                    ts: Sequence[datetime], live_start: datetime) -> int:
        """Store archive windows from history ending >= min_age_days before
        `live_start`. Returns the number of windows stored. Windows are
        skipped if they contain any non-finite value (a replay of accepted
        values can only verbatim-match a contiguous finite stretch)."""
        need = self.cfg.window_ticks
        cutoff = live_start - timedelta(days=self.cfg.min_age_days)
        arr = np.asarray(stage, dtype=float)
        stored = 0
        for end in range(need, len(arr) + 1):
            win = arr[end - need:end]
            if not np.all(np.isfinite(win)):
                continue
            if ts[end - 1] > cutoff:
                continue
            self._windows.append(win)
            stored += 1
        return stored

    def match(self, recent: Sequence[Optional[float]]) -> bool:
        vals = [float(v) for v in recent
                if v is not None and math.isfinite(float(v))]
        if len(vals) != self.cfg.window_ticks:
            return False
        r = np.asarray(vals, dtype=float)
        tol = self.cfg.tol_steps * self.quant_step
        min_range = self.cfg.min_range_steps * self.quant_step
        for win in self._windows:
            if float(np.max(win) - np.min(win)) < min_range:
                continue
            if float(np.max(np.abs(win - r))) <= tol:
                return True
        return False
