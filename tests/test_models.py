"""Tests for stageproof/models.py (T012–T014, T016). Synthetic data only —
no dataset, no network. scipy is never imported (ARCHITECTURE §12)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pytest

from stageproof.domain import Window
from stageproof.models import (RAIN_HOURS, ModelBundle, RatingCurve,
                               RollingRobustZBaseline,
                               StaticThresholdBaseline, TransferModel,
                               build_features, rain_accumulation,
                               upstream_lags)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_window(n=12, rain=True):
    """B(t) = A(t−4) exactly; C(t) = B(t−2) exactly; T is an independent ramp."""
    idx = np.arange(n, dtype=float)
    a = 2.0 + 0.01 * idx
    t = 1.0 + 0.02 * idx
    b = np.concatenate([np.full(4, a[0]), a[:-4]])
    c = np.concatenate([np.full(2, b[0]), b[:-2]])
    rain_hourly = tuple(np.linspace(0.5, 2.0, 48)) if rain else ()
    return Window(
        ts=datetime(2020, 11, 12, 6, 0, tzinfo=timezone.utc),
        tick_idx=n - 1,
        logq={"A": tuple(a), "T": tuple(t), "B": tuple(b), "C": tuple(c)},
        trust={"A": True, "T": True, "B": True},
        rain_hourly=rain_hourly,
        rain_available=rain,
    )


def variant(name, inputs, lags, coef, names):
    return {
        "name": name, "inputs": inputs, "lags": lags,
        "feature_names": names, "mean": [0.0] * len(names),
        "std": [1.0] * len(names), "coef": coef, "intercept": 0.0,
        "alpha": 1.0, "scale_normal": 1.0, "scale_high": 2.0,
        "high_flow_q_threshold": 100.0,
    }


def make_artifact():
    rating = {"type": "power_law", "params": {"a": 2.0, "b": 1.5, "h0": 0.0},
              "range": [0.5, 20.0], "quality": {"log_r2": 0.99, "n": 100}}
    return {
        "model_version": "1", "created_utc": "2026-10-04T00:00:00Z",
        "data_hash": "ab", "config_hash": "cd",
        "tick_minutes": 15, "required_history_ticks": 12,
        "ratings": {r: dict(rating) for r in "ATBC"},
        "lags": {"AB": 4, "TB": 2, "BC": 2},
        "variants": [
            variant("A", ["A"], {"A": [4]}, [1.0], ["A_lnq_l4"]),
            variant("T", ["T"], {"T": [2]}, [1.0], ["T_lnq_l2"]),
        ],
        "downstream": {"name": "downstream", "inputs": ["B"], "lags": {"B": [2]},
                       "feature_names": ["B_lnq_l2"], "mean": [0.0], "std": [1.0],
                       "coef": [1.0], "intercept": 0.0, "alpha": 1.0,
                       "scale_normal": 1.0, "scale_high": 2.0,
                       "high_flow_q_threshold": 100.0},
        "station_params": {r: {"spike_delta_max": 1.0, "noise_baseline_std": 0.01,
                               "quant_step": 0.01} for r in "ATBC"},
        "rain": {"yes_mm": 2.0, "no_mm": 0.2},
        "baseline_stats": {},
        "lag_BC_ticks": 2,
    }


# ---------------------------------------------------------------------------
# T012 RatingCurve
# ---------------------------------------------------------------------------

class TestRatingCurve:
    def test_power_law_fit_and_roundtrip(self):
        h = np.linspace(1.0, 20.0, 400)
        rng = np.random.default_rng(0)
        q = 5.0 * (h - 0.8) ** 1.6 * np.exp(rng.normal(0, 0.005, len(h)))
        curve = RatingCurve.fit(h, q)
        assert curve.kind == "power_law"
        assert curve.quality["log_r2"] > 0.99
        assert abs(curve.params["a"] - 5.0) < 0.2
        assert abs(curve.params["b"] - 1.6) < 0.05
        assert abs(curve.params["h0"] - 0.8) < 0.1
        for probe in (2.0, 7.5, 19.9):
            back = curve.stage_from_q(curve.q_from_stage(probe))
            assert back == pytest.approx(probe, rel=1e-9)

    def test_power_law_extrapolation_follows_power_law(self):
        h = np.linspace(1.0, 20.0, 400)
        q = 5.0 * (h - 0.8) ** 1.6
        curve = RatingCurve.fit(h, q)
        true_q = 5.0 * (25.0 - 0.8) ** 1.6
        assert curve.q_from_stage(25.0) == pytest.approx(true_q, rel=0.02)
        true_q_low = 5.0 * (1.05 - 0.8) ** 1.6
        assert curve.q_from_stage(1.05) == pytest.approx(true_q_low, rel=0.02)

    def test_monotone_fallback_on_kinked_data(self):
        h = np.linspace(1.0, 20.0, 400)
        q = np.where(h < 5.0, h ** 0.3, 5.0 ** 0.3 * (h / 5.0) ** 3.0)
        q *= np.exp(np.random.default_rng(1).normal(0, 0.002, len(h)))
        curve = RatingCurve.fit(h, q)
        assert curve.kind == "monotone"
        grid = np.linspace(1.0, 20.0, 200)
        qg = np.array([curve.q_from_stage(x) for x in grid])
        assert np.all(np.diff(qg) >= -1e-12)
        for probe in (1.5, 4.9, 9.0, 19.5):
            back = curve.stage_from_q(curve.q_from_stage(probe))
            assert back == pytest.approx(probe, rel=1e-9)

    def test_monotone_extrapolation_roundtrip(self):
        h = np.linspace(1.0, 20.0, 400)
        q = np.where(h < 5.0, h ** 0.3, 5.0 ** 0.3 * (h / 5.0) ** 3.0)
        curve = RatingCurve.fit(h, q)
        for probe in (20.5, 25.0):      # beyond the fitted range
            back = curve.stage_from_q(curve.q_from_stage(probe))
            assert back == pytest.approx(probe, rel=1e-9)

    def test_serialization_roundtrip_both_kinds(self):
        h = np.linspace(1.0, 20.0, 200)
        q1 = 5.0 * (h - 0.8) ** 1.6
        q2 = np.where(h < 5.0, h, 5.0 + 4.0 * (h - 5.0))
        for q in (q1, q2):
            curve = RatingCurve.fit(h, q)
            clone = RatingCurve.from_dict(json.loads(json.dumps(curve.to_dict())))
            assert clone.kind == curve.kind
            for probe in (2.0, 10.0, 19.0):
                assert clone.q_from_stage(probe) == pytest.approx(
                    curve.q_from_stage(probe), rel=1e-12)
                assert clone.stage_from_q(probe) == pytest.approx(
                    curve.stage_from_q(probe), rel=1e-12)

    def test_too_few_points_raises(self):
        with pytest.raises(ValueError, match="finite stage/discharge"):
            RatingCurve.fit(np.linspace(1, 5, 10), np.linspace(2, 9, 10))

    def test_lnq_never_nonpositive(self):
        h = np.linspace(1.0, 20.0, 200)
        curve = RatingCurve.fit(h, 5.0 * (h - 0.8) ** 1.6)
        value = curve.lnq_from_stage(-3.0)
        assert math.isfinite(value) and value < -20.0


# ---------------------------------------------------------------------------
# T013 lags and features
# ---------------------------------------------------------------------------

class TestLagsAndFeatures:
    def test_upstream_lags_spacing_and_center(self):
        assert upstream_lags(42) == [21, 28, 35, 42, 49, 56, 63]
        assert upstream_lags(3) == [0, 1, 2, 3, 4, 5, 6]
        assert upstream_lags(0) == [0, 1, 2, 3, 4, 5, 6]
        assert min(upstream_lags(-5)) >= 0

    def test_rain_accumulation(self):
        assert rain_accumulation([1.0, 2.0, 4.0], 1) == pytest.approx(4.0)
        assert rain_accumulation([1.0, 2.0, 4.0], 3) == pytest.approx(7.0)
        assert rain_accumulation([1.0, 2.0, 4.0, 5.0], 3) == pytest.approx(11.0)
        assert rain_accumulation([1.0, float("nan"), 4.0], 3) == pytest.approx(7.5)
        assert rain_accumulation([1.0], 3) is None
        assert rain_accumulation([float("nan")] * 3, 3) is None

    def spec(self, inputs=("A", "T", "R")):
        names, lags = [], {}
        if "A" in inputs:
            lags["A"] = [0, 2]
            names += ["A_lnq_l0", "A_lnq_l2"]
        if "T" in inputs:
            lags["T"] = [1]
            names += ["T_lnq_l1"]
        if "R" in inputs:
            names += [f"rain_ln1p_P{h}" for h in RAIN_HOURS]
        return {"name": "spec", "inputs": list(inputs), "lags": lags,
                "feature_names": names}

    def test_feature_alignment(self):
        window = make_window(n=12)
        feats = build_features(window, self.spec())
        assert feats is not None
        a, t = window.logq["A"], window.logq["T"]
        rain = window.rain_hourly
        expected = [a[-1], a[-3], t[-2]]
        for hours in RAIN_HOURS:
            expected.append(math.log1p(sum(rain[-hours:])))
        assert feats == pytest.approx(np.array(expected))

    def test_rain_sum_over_n_hours(self):
        window = make_window(n=12)
        feats = build_features(window, self.spec())
        rain = window.rain_hourly
        # last 3 hourly values sum
        assert feats[3 + RAIN_HOURS.index(3)] == pytest.approx(
            math.log1p(sum(rain[-3:])))

    def test_missing_value_returns_none(self):
        window = make_window(n=12)
        logq = {k: list(v) for k, v in window.logq.items()}
        logq["A"][-3] = float("nan")
        broken = Window(ts=window.ts, tick_idx=window.tick_idx,
                        logq={k: tuple(v) for k, v in logq.items()},
                        trust=window.trust, rain_hourly=window.rain_hourly,
                        rain_available=True)
        assert build_features(broken, self.spec()) is None

    def test_short_history_returns_none(self):
        window = make_window(n=2)
        assert build_features(window, self.spec(inputs=("A",))) is None

    def test_no_rain_returns_none_for_rain_variant(self):
        window = make_window(n=12, rain=False)
        assert build_features(window, self.spec()) is None
        assert build_features(window, self.spec(inputs=("A",))) is not None

    def test_negative_lag_asserts_no_future(self):
        window = make_window(n=12)
        spec = self.spec(inputs=("A",))
        spec["lags"] = {"A": [-1]}
        spec["feature_names"] = ["A_lnq_lm1"]
        with pytest.raises(AssertionError, match="future"):
            build_features(window, spec)

    def test_feature_count_mismatch_raises(self):
        window = make_window(n=12)
        spec = self.spec(inputs=("A",))
        spec["feature_names"] = ["only_one"]
        with pytest.raises(ValueError, match="feature_names"):
            build_features(window, spec)

    def test_rain_hours_constant(self):
        assert RAIN_HOURS == (1, 3, 6, 12, 24, 48)


# ---------------------------------------------------------------------------
# T014 TransferModel
# ---------------------------------------------------------------------------

class TestTransferModel:
    def _synthetic(self, n=500):
        rng = np.random.default_rng(2)
        # slow drift + substantial iid component so nearby lags are not
        # near-collinear (a pure random walk makes ridge spread the weight)
        x = np.cumsum(rng.normal(0, 0.02, n)) + rng.normal(0, 0.3, n) + 2.0
        y = 0.8 * np.roll(x, 4) + 0.2 + rng.normal(0, 0.02, n)
        y[:4] = y[4]
        lags = upstream_lags(4, 7)          # [1..7], true lag 4 in the middle
        X = np.stack([np.roll(x, l) for l in lags], axis=1)
        return X, y, lags

    def test_recovers_known_lag(self):
        X, y, lags = self._synthetic()
        model = TransferModel.fit(X, y, feature_names=[f"x_l{l}" for l in lags])
        coef = dict(zip(lags, model.coef))
        assert coef[4] == pytest.approx(0.8, abs=0.1)
        for lag in lags:
            if lag != 4:
                assert abs(coef[lag]) < 0.1

    def test_serialization_roundtrip_exact_predictions(self):
        X, y, lags = self._synthetic()
        model = TransferModel.fit(X, y, feature_names=[f"x_l{l}" for l in lags])
        model.scale_normal, model.scale_high = 0.11, 0.22
        clone = TransferModel.from_dict(
            json.loads(json.dumps(model.to_dict())))
        probe = np.random.default_rng(3).normal(2.0, 0.5, (50, X.shape[1]))
        assert np.array_equal(model.predict(probe), clone.predict(probe))
        assert clone.feature_names == model.feature_names
        assert clone.scale_normal == model.scale_normal

    def test_calibration_normal_regime_scale(self):
        rng = np.random.default_rng(4)
        X = rng.normal(2.0, 0.3, (400, 2))
        model = TransferModel.fit(X, X[:, 0])
        y = model.predict(X) + rng.normal(0, 0.1, 400)
        model.calibrate(X, y)
        # 1.4826 · MAD of N(0, 0.1) ≈ 0.1
        assert model.scale_normal == pytest.approx(0.1, rel=0.2)

    def test_calibration_high_regime_split(self):
        rng = np.random.default_rng(5)
        X = rng.normal(2.0, 0.5, (600, 2))
        model = TransferModel.fit(X, X[:, 0])
        y_hat = model.predict(X)
        model.high_flow_q_threshold = float(np.exp(np.percentile(y_hat, 80)))
        y = y_hat + rng.normal(0, 0.1, 600)
        model.calibrate(X, y, min_high=10)
        assert model.scale_normal > 0
        assert model.scale_high > 0
        assert model.regime(float(np.percentile(y_hat, 95))) == "high"
        assert model.regime(float(np.percentile(y_hat, 5))) == "normal"
        assert model.scale_for(float(np.percentile(y_hat, 95))) == model.scale_high

    def test_z_computation(self):
        model = TransferModel(feature_names=["x"], mean=np.array([0.0]),
                              std=np.array([1.0]), coef=np.array([1.0]),
                              intercept=0.0, alpha=1.0, scale_normal=0.5,
                              scale_high=1.0, high_flow_q_threshold=100.0)
        assert model.z(2.1, 2.0) == pytest.approx(0.2)
        model.high_flow_q_threshold = math.exp(3.0)
        assert model.regime(3.5) == "high"
        assert model.z(4.0, 3.5) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# T016 ModelBundle
# ---------------------------------------------------------------------------

class TestModelBundle:
    def test_clean_window_zero_z(self):
        bundle = ModelBundle.from_dict(make_artifact())
        prediction = bundle.predict_target(make_window(n=12))
        assert prediction.error is None
        assert prediction.variant == "A"
        assert prediction.degraded is False
        assert prediction.z == pytest.approx(0.0, abs=1e-9)
        assert prediction.regime == "normal"
        assert prediction.scale == 1.0
        assert prediction.q_pred == pytest.approx(
            math.exp(make_window(n=12).logq["B"][-1]))
        assert prediction.stage_pred is not None

    def test_untrusted_input_switches_variant(self):
        bundle = ModelBundle.from_dict(make_artifact())
        window = make_window(n=12)
        prediction = bundle.predict_target(window, trust={"A": False, "T": True})
        assert prediction.variant == "T"
        assert prediction.degraded is True
        assert prediction.inputs_used == ("T",)

    def test_no_usable_variant_reports_error(self):
        bundle = ModelBundle.from_dict(make_artifact())
        prediction = bundle.predict_target(make_window(n=12),
                                           trust={"A": False, "T": False})
        assert prediction.variant == "none"
        assert prediction.error
        assert prediction.degraded is True

    def test_downstream_prediction_zero_z(self):
        bundle = ModelBundle.from_dict(make_artifact())
        prediction = bundle.predict_downstream(make_window(n=12))
        assert prediction is not None
        assert prediction.role == "C"
        assert prediction.z == pytest.approx(0.0, abs=1e-9)

    def test_downstream_insufficient_history_returns_none_features(self):
        bundle = ModelBundle.from_dict(make_artifact())
        short = Window(ts=datetime(2020, 11, 12, 6, 0, tzinfo=timezone.utc),
                       tick_idx=0, logq={"B": (1.0,)},
                       rain_available=True)
        prediction = bundle.predict_downstream(short)
        assert prediction is not None
        assert prediction.error       # build_features returned None → no variant

    def test_rating_helpers(self):
        bundle = ModelBundle.from_dict(make_artifact())
        q = bundle.q_from_stage("B", 4.0)
        assert q == pytest.approx(2.0 * 4.0 ** 1.5)
        assert bundle.stage_from_q("B", q) == pytest.approx(4.0)
        assert bundle.lnq_from_stage("B", 4.0) == pytest.approx(math.log(q))

    def test_bundle_metadata(self):
        bundle = ModelBundle.from_dict(make_artifact())
        assert bundle.tick_minutes == 15
        assert bundle.required_history_ticks == 12
        assert bundle.travel_lags["AB"] == 4
        assert bundle.lag_BC_ticks == 2
        assert bundle.station_params["B"]["quant_step"] == 0.01
        assert bundle.rain["yes_mm"] == 2.0
        assert set(bundle.ratings) == {"A", "T", "B", "C"}


# ---------------------------------------------------------------------------
# T017 — baselines (ARCHITECTURE §6.10)
# ---------------------------------------------------------------------------

def test_static_threshold_fires_exactly_at_action_stage():
    b = StaticThresholdBaseline(action_stage=14.0)
    assert b.update(13.99) is False
    assert b.update(14.0) is True
    assert b.update(20.0) is True
    assert b.update(None) is False


def test_rolling_robust_z_flags_sharp_rise():
    rollz = RollingRobustZBaseline(window_ticks=96, rollz_threshold=6.0,
                                   quant_step=0.01)
    flagged = False
    for _ in range(96):
        f, z = rollz.update(5.0)
        assert not f
    for i in range(1, 5):
        f, z = rollz.update(5.0 + 0.5 * i)   # sharp 2 ft rise in 4 ticks
        flagged = flagged or f
    assert flagged and rollz.last_value > 6.0


def test_rolling_robust_z_quiet_series_never_flags():
    rollz = RollingRobustZBaseline(window_ticks=96, rollz_threshold=6.0,
                                   quant_step=0.01)
    rng = np.random.default_rng(42)
    for _ in range(300):
        f, z = rollz.update(5.0 + 0.008 * float(rng.standard_normal()))
        assert not f


def test_rolling_robust_z_skips_nonfinite_and_pads_mad():
    rollz = RollingRobustZBaseline(window_ticks=8, rollz_threshold=6.0,
                                   quant_step=0.01)
    f, z = rollz.update(None)
    assert f is False and z is None
    for _ in range(4):
        rollz.update(5.0)            # constant series: MAD 0 -> floored
    f, z = rollz.update(5.0 + 0.3)   # one 0.3 ft jump out of the flat level
    assert f is True and abs(z - (0.3 / (1.4826 * 0.01))) < 1e-9
