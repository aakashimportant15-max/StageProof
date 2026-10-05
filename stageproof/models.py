"""Rating curves, feature builder, transfer model, model bundle (T012–T014, T016).

ARCHITECTURE §6: everything operates on rating-converted ln Q; Ridge-only
integrity model; no future access (§6.9 — build_features asserts its maximum
accessed index is ≤ the current index). This module imports only `domain`
plus third-party libs (ARCHITECTURE §3).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import TimeSeriesSplit

from .domain import Prediction, Window

__all__ = [
    "RatingCurve", "TransferModel", "ModelBundle", "VariantSpec",
    "build_features", "upstream_lags", "rain_accumulation", "RAIN_HOURS",
    "StaticThresholdBaseline", "RollingRobustZBaseline",
]

TICK_MINUTES = 15
RAIN_HOURS: tuple[int, ...] = (1, 3, 6, 12, 24, 48)
MAD_SCALE = 1.4826

#: Engineering choice (recorded in fit_report/MEMORY): a power-law rating is
#: accepted when its log-space R² reaches this; below it the monotone
#: interpolation fallback is stored instead (ARCHITECTURE §6.3).
POWER_LAW_MIN_LOG_R2 = 0.95

#: Engineering choice: RidgeCV alpha grid (ARCHITECTURE §6.4).
ALPHA_GRID = tuple(np.logspace(-3, 3, 13))

#: Engineering choice: sample weight applied to rows above the training 90th
#: percentile of Q_B when fit.highflow_weight is enabled (§6.4 remediation).
HIGHFLOW_SAMPLE_WEIGHT = 5.0


def _mad(values: np.ndarray) -> float:
    med = np.median(values)
    return float(np.median(np.abs(values - med)))


# ---------------------------------------------------------------------------
# T012 — RatingCurve
# ---------------------------------------------------------------------------

class RatingCurve:
    """Stage ↔ discharge conversion (ARCHITECTURE §6.3).

    kind="power_law": Q = a·(h − h0)^b (exact analytic inverse; extrapolation
    beyond the observed range follows the power law).
    kind="monotone": piecewise-linear monotone interpolation through stored
    knots (its piecewise-linear inverse is exact); extrapolation follows a
    log-log power law fitted on the end segments.
    """

    def __init__(self, kind: str, params: dict, h_range: Sequence[float],
                 q_range: Sequence[float], quality: Optional[dict] = None):
        self.kind = kind
        self.params = params
        self.h_range = (float(h_range[0]), float(h_range[1]))
        self.q_range = (float(q_range[0]), float(q_range[1]))
        self.quality = dict(quality or {})

    # -- fitting ------------------------------------------------------------

    @classmethod
    def fit(cls, stage, discharge, h0_grid: Optional[np.ndarray] = None,
            min_points: int = 30) -> "RatingCurve":
        h = np.asarray(stage, dtype=float)
        q = np.asarray(discharge, dtype=float)
        ok = np.isfinite(h) & np.isfinite(q) & (q > 0)
        h, q = h[ok], q[ok]
        order = np.argsort(h)
        h, q = h[order], q[order]
        if len(h) < min_points:
            raise ValueError(
                f"rating fit needs >= {min_points} finite stage/discharge "
                f"pairs, got {len(h)}")
        best = cls._best_power_law(h, q, h0_grid)
        if best is not None and best["log_r2"] >= POWER_LAW_MIN_LOG_R2:
            return cls("power_law",
                       {"a": best["a"], "b": best["b"], "h0": best["h0"]},
                       (h[0], h[-1]), (q[0], q[-1]),
                       quality={"log_r2": best["log_r2"], "n": int(len(h)),
                                "h0_grid": "searched"})
        knots = cls._monotone_knots(h, q)
        tails = {"low": cls._tail_power_law(h, q, "low"),
                 "high": cls._tail_power_law(h, q, "high")}
        return cls("monotone", {"knots_h": knots[0].tolist(),
                                "knots_q": knots[1].tolist(), "tails": tails},
                   (h[0], h[-1]), (q[0], q[-1]),
                   quality={"log_r2": (best or {}).get("log_r2", float("nan")),
                            "n": int(len(h)), "h0_grid": "searched"})

    @staticmethod
    def _best_power_law(h: np.ndarray, q: np.ndarray,
                        h0_grid: Optional[np.ndarray]) -> Optional[dict]:
        lo = min(0.0, float(h.min()) - 1.0)
        if h0_grid is None:
            h0_grid = np.linspace(lo, float(h.min()) - 1e-4, 60)
        best: Optional[dict] = None
        ly = np.log(q)
        for h0 in h0_grid:
            d = h - float(h0)
            mask = d > 0
            if mask.sum() < 10:
                continue
            x = np.log(d[mask])
            y = ly[mask]
            b, log_a = np.polyfit(x, y, 1)
            resid = y - (b * x + log_a)
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            if ss_tot <= 0:
                continue
            r2 = 1.0 - float(np.sum(resid ** 2)) / ss_tot
            if best is None or r2 > best["log_r2"]:
                best = {"log_r2": r2, "h0": float(h0), "b": float(b),
                        "a": float(np.exp(log_a))}
        return best

    @staticmethod
    def _monotone_knots(h: np.ndarray, q: np.ndarray,
                        max_knots: int = 128) -> tuple[np.ndarray, np.ndarray]:
        uniq, inverse = np.unique(h, return_inverse=True)
        sums = np.zeros(len(uniq))
        counts = np.zeros(len(uniq))
        np.add.at(sums, inverse, q)
        np.add.at(counts, inverse, 1.0)
        yq = sums / counts
        yq = np.maximum.accumulate(yq)
        if len(uniq) > max_knots:
            idx = np.unique(np.linspace(0, len(uniq) - 1, max_knots).astype(int))
            return uniq[idx], yq[idx]
        return uniq, yq

    @staticmethod
    def _tail_power_law(h: np.ndarray, q: np.ndarray, side: str) -> dict:
        n = max(5, int(len(h) * 0.15))
        idx = slice(0, n) if side == "low" else slice(-n, None)
        x = np.log(h[idx])
        y = np.log(q[idx])
        b, log_a = np.polyfit(x, y, 1)
        return {"a": float(np.exp(log_a)), "b": float(b)}

    # -- evaluation ----------------------------------------------------------

    def q_from_stage(self, h: float) -> float:
        h = float(h)
        if self.kind == "power_law":
            p = self.params
            return p["a"] * max(h - p["h0"], 1e-6) ** p["b"]
        knots_h = np.asarray(self.params["knots_h"])
        knots_q = np.asarray(self.params["knots_q"])
        if h < knots_h[0]:
            tail = self.params["tails"]["low"]
            return max(tail["a"] * max(h, 1e-6) ** tail["b"], 1e-6)
        if h > knots_h[-1]:
            tail = self.params["tails"]["high"]
            return tail["a"] * h ** tail["b"]
        return float(np.interp(h, knots_h, knots_q))

    def stage_from_q(self, q: float) -> float:
        q = float(q)
        if self.kind == "power_law":
            p = self.params
            return p["h0"] + max(q, 1e-9) ** (1.0 / p["b"]) / p["a"] ** (1.0 / p["b"])
        knots_h = np.asarray(self.params["knots_h"])
        knots_q = np.asarray(self.params["knots_q"])
        if q < knots_q[0]:
            tail = self.params["tails"]["low"]
            return max(q, 1e-9) ** (1.0 / tail["b"]) / tail["a"] ** (1.0 / tail["b"])
        if q > knots_q[-1]:
            tail = self.params["tails"]["high"]
            return q ** (1.0 / tail["b"]) / tail["a"] ** (1.0 / tail["b"])
        return float(np.interp(q, knots_q, knots_h))

    def lnq_from_stage(self, h: float) -> float:
        return math.log(max(self.q_from_stage(h), 1e-9))

    def q_from_stage_many(self, h) -> np.ndarray:
        """Vectorized q_from_stage with identical semantics."""
        h = np.asarray(h, dtype=float)
        if self.kind == "power_law":
            p = self.params
            return p["a"] * np.maximum(h - p["h0"], 1e-6) ** p["b"]
        knots_h = np.asarray(self.params["knots_h"])
        knots_q = np.asarray(self.params["knots_q"])
        q = np.interp(h, knots_h, knots_q)
        low = h < knots_h[0]
        high = h > knots_h[-1]
        tl = self.params["tails"]["low"]
        th = self.params["tails"]["high"]
        q = np.where(low, np.maximum(tl["a"] * np.maximum(h, 1e-6) ** tl["b"], 1e-6), q)
        q = np.where(high, th["a"] * np.maximum(h, 1e-6) ** th["b"], q)
        return q

    # -- serialization (ARCHITECTURE §6.8: ratings{role:{type,params,range}}) --

    def to_dict(self) -> dict:
        return {"type": self.kind, "params": self.params,
                "range": [self.h_range[0], self.h_range[1]],
                "quality": self.quality}

    @classmethod
    def from_dict(cls, d: dict) -> "RatingCurve":
        return cls(d["type"], dict(d["params"]), d["range"], d["range"],
                   quality=d.get("quality"))


# ---------------------------------------------------------------------------
# T013 — lag grid and feature builder
# ---------------------------------------------------------------------------

def upstream_lags(tau_ticks: float, n_lags: int = 7,
                  divisor: int = 6) -> list[int]:
    """n_lags ticks centered on the travel time τ, spaced by
    max(1, round(τ/divisor)); shifted up (never down) when the centered grid
    would reach negative lags (ARCHITECTURE §6.4). divisor=4 is the §6.7
    lag-grid remediation recorded in docs/MEMORY.md §26."""
    tau = int(round(float(tau_ticks)))
    spacing = max(1, int(round(tau / float(divisor)))) if tau > 0 else 1
    center = n_lags // 2
    grid = [tau + (j - center) * spacing for j in range(n_lags)]
    lo = min(grid)
    if lo < 0:
        grid = [g - lo for g in grid]
    return sorted(set(grid))


def rain_accumulation(hourly: Sequence[float], hours: int) -> Optional[float]:
    """Sum of the last `hours` completed hourly values (ARCHITECTURE §6.4).
    Missing hours are skipped; returns None if no values are available."""
    vals = list(hourly)[-hours:]
    if len(vals) < hours:
        return None
    arr = np.asarray(vals, dtype=float)
    if np.all(~np.isfinite(arr)):
        return None
    return float(np.nanmean(arr)) * hours


def build_features(window: Window, variant_spec: dict) -> Optional[np.ndarray]:
    """Feature vector for the LAST row of `window`, in the order of
    variant_spec["feature_names"]. Returns None if any needed lag is
    unavailable (insufficient history or missing data). Asserts that no
    accessed index exceeds the current index (ARCHITECTURE §6.9 / AC-NL)."""
    inputs = list(variant_spec.get("inputs", []))
    lags = variant_spec.get("lags") or {}
    names = variant_spec.get("feature_names") or []
    values: list[float] = []
    for role in inputs:
        if role == "R":
            continue
        series = window.logq.get(role) or ()
        for lag in (lags.get(role) or []):
            lag = int(lag)
            assert lag >= 0, (
                f"negative lag {lag} for input '{role}' would access the "
                f"future (ARCHITECTURE §6.9)")
            idx = len(series) - 1 - lag
            if idx < 0:
                return None
            value = series[idx]
            if value is None or not math.isfinite(float(value)):
                return None
            values.append(float(value))
    if "R" in inputs:
        if not window.rain_available:
            return None
        for hours in RAIN_HOURS:
            total = rain_accumulation(window.rain_hourly, hours)
            if total is None:
                return None
            values.append(math.log1p(max(total, 0.0)))
    if names and len(values) != len(names):
        raise ValueError(
            f"variant spec '{variant_spec.get('name', '?')}': built "
            f"{len(values)} features but feature_names has {len(names)}")
    return np.asarray(values, dtype=float)


# ---------------------------------------------------------------------------
# T014 — TransferModel
# ---------------------------------------------------------------------------

@dataclass
class TransferModel:
    """Ridge transfer model ln Q_B ~ upstream ln Q lags + rain (§6.4–6.5)."""
    feature_names: list[str]
    mean: np.ndarray
    std: np.ndarray
    coef: np.ndarray
    intercept: float
    alpha: float
    scale_normal: float = 1.0
    scale_high: float = 1.0
    high_flow_q_threshold: float = float("inf")

    @classmethod
    def fit(cls, X, y, q_target: Optional[np.ndarray] = None,
            feature_names: Optional[list[str]] = None,
            highflow_weight: bool = False,
            alpha_grid: Sequence[float] = ALPHA_GRID) -> "TransferModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        mean = X.mean(axis=0)
        std = np.maximum(X.std(axis=0), 1e-12)
        Xs = (X - mean) / std
        sample_weight = None
        if highflow_weight and q_target is not None:
            thr = float(np.percentile(np.asarray(q_target, float), 90))
            sample_weight = np.where(np.asarray(q_target, float) > thr,
                                     HIGHFLOW_SAMPLE_WEIGHT, 1.0)
        ridge = RidgeCV(alphas=np.asarray(alpha_grid),
                        cv=TimeSeriesSplit(n_splits=5))
        ridge.fit(Xs, y, sample_weight=sample_weight)
        # de-standardize so stored coefficients are interpretable in ln-Q
        # units (identical predictions: Xs @ cs + b = X @ (cs/std) + b')
        coef = ridge.coef_ / std
        intercept = float(ridge.intercept_ - mean @ coef)
        q_thr = (float(np.percentile(np.asarray(q_target, float), 90))
                 if q_target is not None and len(q_target) else float("inf"))
        return cls(
            feature_names=list(feature_names or []),
            mean=mean, std=std,
            coef=np.asarray(coef, dtype=float),
            intercept=intercept,
            alpha=float(ridge.alpha_),
            high_flow_q_threshold=q_thr,
        )

    def calibrate(self, X_cal, y_cal, min_high: int = 10) -> None:
        """Robust per-regime scale on the held-out calibration range (§6.5)."""
        X_cal = np.asarray(X_cal, dtype=float)
        y_cal = np.asarray(y_cal, dtype=float)
        y_hat = self.predict(X_cal)
        resid = y_cal - y_hat
        high = y_hat >= math.log(self.high_flow_q_threshold) \
            if math.isfinite(self.high_flow_q_threshold) else \
            np.zeros(len(y_hat), dtype=bool)
        s_normal = MAD_SCALE * _mad(resid[~high])
        if high.sum() >= min_high:
            s_high = MAD_SCALE * _mad(resid[high])
        else:
            s_high = s_normal
        self.scale_normal = max(s_normal, 1e-9)
        self.scale_high = max(s_high, 1e-9)

    def predict(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        return X @ self.coef + self.intercept

    def predict_one(self, features: np.ndarray) -> float:
        return float(self.predict(np.asarray(features, float)[None, :])[0])

    def regime(self, y_hat: float) -> str:
        if math.isfinite(self.high_flow_q_threshold) and \
                y_hat >= math.log(self.high_flow_q_threshold):
            return "high"
        return "normal"

    def scale_for(self, y_hat: float) -> float:
        return self.scale_high if self.regime(y_hat) == "high" \
            else self.scale_normal

    def z(self, y_obs: float, y_hat: float) -> float:
        return (float(y_obs) - float(y_hat)) / self.scale_for(float(y_hat))

    # -- serialization (§6.8 variant keys) -----------------------------------

    def to_dict(self) -> dict:
        return {
            "feature_names": list(self.feature_names),
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "coef": self.coef.tolist(),
            "intercept": float(self.intercept),
            "alpha": float(self.alpha),
            "scale_normal": float(self.scale_normal),
            "scale_high": float(self.scale_high),
            "high_flow_q_threshold": float(self.high_flow_q_threshold),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TransferModel":
        return cls(
            feature_names=list(d["feature_names"]),
            mean=np.asarray(d["mean"], dtype=float),
            std=np.asarray(d["std"], dtype=float),
            coef=np.asarray(d["coef"], dtype=float),
            intercept=float(d["intercept"]),
            alpha=float(d["alpha"]),
            scale_normal=float(d["scale_normal"]),
            scale_high=float(d["scale_high"]),
            high_flow_q_threshold=float(d["high_flow_q_threshold"]),
        )


@dataclass
class VariantSpec:
    """A named variant: inputs + lag grid + fitted transfer model."""
    name: str
    inputs: tuple[str, ...]
    lags: dict[str, list[int]]
    model: TransferModel

    def spec_dict(self) -> dict:
        return {"name": self.name, "inputs": list(self.inputs),
                "lags": {k: list(v) for k, v in self.lags.items()},
                "feature_names": list(self.model.feature_names)}


# ---------------------------------------------------------------------------
# T016 — ModelBundle
# ---------------------------------------------------------------------------

def _trusted(trust: Any, role: str) -> bool:
    if isinstance(trust, dict):
        return bool(trust.get(role, False))
    return role in set(trust or ())


class ModelBundle:
    """Inference API over artifacts/model.json (ARCHITECTURE §6.8, T016)."""

    def __init__(self, artifact: dict):
        self.artifact = artifact
        self.tick_minutes = int(artifact["tick_minutes"])
        self.required_history_ticks = int(artifact["required_history_ticks"])
        self.travel_lags = dict(artifact.get("lags") or {})
        self.lag_BC_ticks = artifact.get("lag_BC_ticks")
        self.ratings = {role: RatingCurve.from_dict(d)
                        for role, d in (artifact.get("ratings") or {}).items()}
        self.variants: list[VariantSpec] = [
            VariantSpec(name=v["name"], inputs=tuple(v["inputs"]),
                        lags={k: list(l) for k, l in (v.get("lags") or {}).items()},
                        model=TransferModel.from_dict(v))
            for v in artifact["variants"]]
        down = artifact.get("downstream") or {}
        self.downstream: Optional[VariantSpec] = None
        if down:
            self.downstream = VariantSpec(
                name=down.get("name", "downstream"),
                inputs=tuple(down.get("inputs", ["B", "R"])),
                lags={k: list(l) for k, l in (down.get("lags") or {}).items()},
                model=TransferModel.from_dict(down))
        self.station_params = dict(artifact.get("station_params") or {})
        self.rain = dict(artifact.get("rain") or {})
        self.baseline_stats = dict(artifact.get("baseline_stats") or {})

    @classmethod
    def from_dict(cls, artifact: dict) -> "ModelBundle":
        return cls(artifact)

    # -- stage ↔ Q helpers (§6.3) --------------------------------------------

    def q_from_stage(self, role: str, h: float) -> float:
        return self.ratings[role].q_from_stage(h)

    def stage_from_q(self, role: str, q: float) -> float:
        return self.ratings[role].stage_from_q(q)

    def lnq_from_stage(self, role: str, h: float) -> float:
        return self.ratings[role].lnq_from_stage(h)

    # -- inference -------------------------------------------------------------

    def predict_target(self, window: Window, trust: Any = None) -> Prediction:
        try:
            return self._predict(window, self.variants,
                                 trust if trust is not None else window.trust)
        except Exception as exc:  # noqa: BLE001 — doc: exceptions → Prediction.error
            return Prediction(role="B", ts=window.ts, variant="none",
                              degraded=True, error=f"{type(exc).__name__}: {exc}")

    def predict_downstream(self, window: Window) -> Optional[Prediction]:
        if self.downstream is None:
            return None
        try:
            prediction = self._predict(window, [self.downstream], None,
                                       role="C")
            return prediction
        except Exception as exc:  # noqa: BLE001
            return Prediction(role="C", ts=window.ts, variant=self.downstream.name,
                              degraded=True, error=f"{type(exc).__name__}: {exc}")

    def _predict(self, window: Window, candidates: list[VariantSpec],
                 trust: Any, role: str = "B") -> Prediction:
        chosen: Optional[tuple[VariantSpec, np.ndarray, TransferModel]] = None
        for spec in candidates:
            needed = [r for r in spec.inputs if r != "R"]
            if trust is not None and not all(_trusted(trust, r) for r in needed):
                continue
            features = build_features(window, spec.spec_dict())
            if features is None:
                continue
            chosen = (spec, features, spec.model)
            break
        series = window.logq.get(role) or ()
        y_obs = float(series[-1]) if series and math.isfinite(float(series[-1])) \
            else None
        if chosen is None:
            return Prediction(role=role, ts=window.ts, variant="none",
                              degraded=True,
                              error="no variant available: inputs untrusted or "
                                    "lag history incomplete")
        spec, features, model = chosen
        y_hat = model.predict_one(features)
        scale = model.scale_for(y_hat)
        z = model.z(y_obs, y_hat) if y_obs is not None else None
        q_pred = math.exp(y_hat)
        stage_pred = None
        if role in self.ratings:
            stage_pred = self.stage_from_q(role, q_pred)
        return Prediction(
            role=role, ts=window.ts, variant=spec.name,
            inputs_used=tuple(spec.inputs),
            logq_obs=y_obs, logq_pred=y_hat, scale=scale,
            regime=model.regime(y_hat), z=z, q_pred=q_pred,
            stage_pred=stage_pred,
            degraded=(candidates is not None and spec.name
                      != candidates[0].name),
        )


# ---------------------------------------------------------------------------
# T017 — baselines (comparison only, never StageProof behavior; §6.10)
# ---------------------------------------------------------------------------

class StaticThresholdBaseline:
    """Alarm when the observed B stage reaches `action_stage` (§6.10).

    Pure, online, no model dependency. Stands in for the "a plain level
    alarm would have cried here" comparison on the dashboard and in
    evaluation.
    """

    def __init__(self, action_stage: float):
        self.action_stage = float(action_stage)

    def update(self, stage: Optional[float]) -> bool:
        if stage is None:
            return False
        v = float(stage)
        return math.isfinite(v) and v >= self.action_stage


class RollingRobustZBaseline:
    """Generic single-sensor anomaly detector (§6.10).

    `z = (stage − median(W)) / (1.4826 · MAD(W))` over the last
    `window_ticks` accepted readings; MAD floored at one quantization step;
    flag when `|z| > rollz_threshold`. `update` returns `(flag, z_value)`.
    """

    def __init__(self, window_ticks: int, rollz_threshold: float,
                 quant_step: float = 0.01):
        self.window_ticks = max(int(window_ticks), 1)
        self.rollz_threshold = float(rollz_threshold)
        self.quant_step = max(float(quant_step), 1e-9)
        self._hist: deque = deque(maxlen=self.window_ticks)
        self.last_value: Optional[float] = None

    def update(self, stage: Optional[float]) -> tuple[bool, Optional[float]]:
        if stage is None:
            return (False, None)
        v = float(stage)
        if not math.isfinite(v):
            return (False, None)
        self._hist.append(v)
        window = np.asarray(self._hist, dtype=float)
        mad = max(_mad(window), self.quant_step)
        z = (v - float(np.median(window))) / (MAD_SCALE * mad)
        self.last_value = z
        return (abs(z) > self.rollz_threshold, z)
