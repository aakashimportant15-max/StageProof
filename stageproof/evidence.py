"""Per-tick evidence assembly (T021; RULES §3.3-§3.6, §3.8-§3.10).

The builder turns one tick of raw facts into a single typed `Evidence`:
context classification, upstream trend, rain support, downstream response,
drift, and notability. It makes NO judgments about labels — that is the
decision engine's job (RULES §4). Imports only domain, settings, checks
(ARCHITECTURE §3).
"""

from __future__ import annotations

import math
from typing import Optional

from .checks import (HealthResult, drift_measure, is_notable, rain_support,
                     upstream_trend)
from .domain import (Context, Downstream, Evidence, Prediction, Window)
from .settings import Thresholds

__all__ = ["EvidenceBuilder"]


class EvidenceBuilder:
    """Stateful across ticks: keeps z / z_down residual histories and the
    current notable-episode start tick (RULES §3.5 PENDING logic)."""

    def __init__(self, thresholds: Thresholds, watch_stage: float,
                 tick_minutes: int, lag_BC_ticks: Optional[int],
                 yes_mm: Optional[float], no_mm: Optional[float]):
        self._ctx = thresholds.context
        self._trend_cfg = thresholds.trend
        self._rain_cfg = thresholds.rain
        self._drift_cfg = thresholds.drift
        self._drift_window_ticks = thresholds.persistence.fault_attack_ticks
        self._watch_stage = float(watch_stage)
        self._tick_minutes = int(tick_minutes)
        self._lag_BC_ticks = int(lag_BC_ticks) if lag_BC_ticks else 0
        self._yes_mm = yes_mm
        self._no_mm = no_mm
        self._z_hist: list[float] = []
        self._resid_hist: list[float] = []   # raw ln-Q residuals (RULES §3.9)
        self._z_down_hist: list[float] = []
        self._episode_start: Optional[int] = None
        self._prev_notable = False

    # -- z helpers ---------------------------------------------------------

    def _floored_z(self, pred: Optional[Prediction],
                   scale_floor: float) -> Optional[float]:
        """RULES §3.3: z = (obs - pred) / s with s floored by scale_floor.
        Prediction.z is unfloored, so recompute from the raw fields."""
        if pred is None or pred.error or pred.logq_obs is None \
                or pred.logq_pred is None or pred.scale is None:
            return None
        scale = max(float(pred.scale), float(scale_floor))
        return (float(pred.logq_obs) - float(pred.logq_pred)) / scale

    def _mean(self, hist: list[float]) -> Optional[float]:
        if not hist:
            return None
        win = hist[-self._ctx.z_window_ticks:]
        return sum(win) / len(win)

    # -- context (RULES §3.3) ----------------------------------------------

    def _context(self, pred: Optional[Prediction], z_eff: Optional[float],
                 z_mean: Optional[float]) -> Context:
        if pred is None or pred.error or z_eff is None or z_mean is None:
            return Context.INSUFFICIENT
        inputs = set(pred.inputs_used or ())
        if inputs and inputs <= {"R"}:
            return Context.INSUFFICIENT
        if z_mean >= self._ctx.z_implausible:
            return Context.PHANTOM
        if z_mean <= -self._ctx.z_implausible:
            return Context.SUPPRESSED
        if (inputs & {"A", "T"}) and abs(z_mean) <= self._ctx.z_consistent:
            return Context.CONSISTENT
        return Context.AMBIGUOUS

    # -- downstream (RULES §3.5) -------------------------------------------

    def _downstream(self, window: Window, down_pred: Optional[Prediction],
                    notable: bool, tick_idx: int) -> tuple[Downstream, Optional[float]]:
        z_down = self._floored_z(down_pred, self._ctx.scale_floor)
        if z_down is not None:
            self._z_down_hist.append(z_down)
        z_down_mean = self._mean(self._z_down_hist)

        if down_pred is None or down_pred.error \
                or not bool((window.trust or {}).get("C", False)):
            return Downstream.UNKNOWN, z_down_mean

        if notable and self._episode_start is not None \
                and tick_idx - self._episode_start < self._lag_BC_ticks:
            return Downstream.PENDING, z_down_mean

        if z_down_mean is None:
            return Downstream.UNKNOWN, z_down_mean
        if abs(z_down_mean) <= self._ctx.z_consistent:
            return Downstream.YES, z_down_mean
        if abs(z_down_mean) >= self._ctx.z_implausible:
            return Downstream.NO, z_down_mean
        return Downstream.UNKNOWN, z_down_mean

    # -- main entry ----------------------------------------------------------

    def build(self, window: Window, prediction: Optional[Prediction],
              downstream: Optional[Prediction],
              transport_flags: dict, transport_hard: dict,
              health: dict[str, HealthResult],
              replay_match: bool = False) -> Evidence:
        tick_idx = window.tick_idx

        b_series = window.stage.get("B") or ()
        obs_stage: Optional[float] = None
        if b_series and math.isfinite(float(b_series[-1])):
            obs_stage = float(b_series[-1])
        pred_stage: Optional[float] = None
        if prediction is not None and not prediction.error \
                and prediction.stage_pred is not None \
                and math.isfinite(prediction.stage_pred):
            pred_stage = float(prediction.stage_pred)

        notable = is_notable(obs_stage, pred_stage, self._watch_stage)
        if notable and not self._prev_notable:
            self._episode_start = tick_idx
        elif not notable:
            self._episode_start = None
        self._prev_notable = notable

        z_eff = self._floored_z(prediction, self._ctx.scale_floor)
        if z_eff is not None:
            self._z_hist.append(z_eff)
            self._resid_hist.append(float(prediction.logq_obs)
                                    - float(prediction.logq_pred))
        z_mean = self._mean(self._z_hist)
        context = self._context(prediction, z_eff, z_mean)

        drift = False
        if context in (Context.PHANTOM, Context.SUPPRESSED):
            # RULES §3.9: |mean residual| in ln Q over the persistence window.
            drift = drift_measure(self._resid_hist, self._tick_minutes,
                                  self._drift_cfg, self._drift_window_ticks)

        downstream_state, z_down_mean = self._downstream(
            window, downstream, notable, tick_idx)

        b_health = health.get("B")
        return Evidence(
            ts=window.ts,
            tick_idx=tick_idx,
            transport_flags={r: tuple(f) for r, f in (transport_flags or {}).items()},
            transport_hard={r: tuple(f) for r, f in (transport_hard or {}).items()},
            health_flags={r: hr.flags for r, hr in (health or {}).items()},
            shape_ok=None if b_health is None else b_health.shape_ok,
            rate_exceeded=bool(b_health.rate_exceeded) if b_health else False,
            variant=prediction.variant if prediction else None,
            z=z_eff,
            z_mean=z_mean,
            context=context,
            up_trend=upstream_trend(window, self._trend_cfg),
            rain=rain_support(window, self._rain_cfg, self._yes_mm, self._no_mm),
            downstream=downstream_state,
            z_down_mean=z_down_mean,
            replay_match=bool(replay_match),
            noise_too_clean=bool(b_health.noise_too_clean) if b_health else False,
            drift=drift,
            obs_stage=obs_stage,
            pred_stage=pred_stage,
            notable=notable,
            degraded=bool(prediction.degraded) if prediction else False,
            trusted_inputs=dict(window.trust or {}),
        )
