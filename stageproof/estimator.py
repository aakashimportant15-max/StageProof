"""Substitute estimate from the transfer model (T025; RULES §10).

The estimate is the same model's prediction converted to a stage interval via
the B rating inverse — it never touches B's own readings (ARCHITECTURE §3:
this module must not use the target sensor's values; §10.1 uses only trusted
upstream/tributary/rain inputs through `Prediction.logq_pred`).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .domain import Conf, Estimate, Prediction
from .models import ModelBundle

__all__ = ["make_estimate", "confidence_for"]


def confidence_for(variant: str) -> Conf:
    """RULES §10.3: HIGH = contains both A and T; MED = exactly one of
    A/T; LOW = rain-only; NONE = no model could run."""
    roles = {part.strip().upper() for part in variant.split("+") if part.strip()}
    if not roles or roles == {"NONE"}:
        return Conf.NONE
    if "R" in roles and roles.isdisjoint({"A", "T"}):
        return Conf.LOW
    if {"A", "T"} <= roles:
        return Conf.HIGH
    if roles & {"A", "T"}:
        return Conf.MED
    return Conf.NONE


def make_estimate(prediction: Prediction, bundle: ModelBundle,
                  in_use: bool, cfg: Any) -> Estimate:
    """Build the `Estimate` for one tick from an already-computed
    `Prediction` (RULES §10.1): `q_hat = exp(logq_pred)`;
    `[stage_lo, stage_hi]` = B-rating inverse of
    `logq_pred ± band_z × scale`."""
    if prediction is None or prediction.error is not None or \
            prediction.logq_pred is None or not \
            math.isfinite(float(prediction.logq_pred)):
        return Estimate(ts=prediction.ts if prediction is not None
                        else None,
                        variant=prediction.variant if prediction is not None
                        else "",
                        confidence=Conf.NONE, in_use=in_use)

    band_z = float(cfg.threshold("context", "band_z"))
    scale = prediction.scale
    center = float(prediction.logq_pred)
    q_hat = math.exp(center)

    stage_hat = bundle.stage_from_q("B", q_hat)
    stage_lo = stage_hi = None
    if scale is not None and math.isfinite(float(scale)):
        stage_lo = bundle.stage_from_q("B", math.exp(center - band_z * float(scale)))
        stage_hi = bundle.stage_from_q("B", math.exp(center + band_z * float(scale)))

    return Estimate(
        ts=prediction.ts,
        stage_hat=stage_hat,
        stage_lo=stage_lo,
        stage_hi=stage_hi,
        q_hat=q_hat,
        variant=prediction.variant,
        basis=tuple(prediction.inputs_used),
        confidence=confidence_for(prediction.variant),
        in_use=in_use,
    )
