"""Tests for stageproof/estimator.py (T025; RULES §10). Synthetic only."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stageproof.domain import Conf, Prediction
from stageproof.estimator import confidence_for, make_estimate
from stageproof.models import ModelBundle, RatingCurve
from stageproof.settings import load_settings

TS0 = datetime(2020, 11, 12, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def settings():
    return load_settings("config", ".env")


@pytest.fixture(scope="module")
def bundle():
    rating = RatingCurve(kind="power_law", params={"a": 12.0, "b": 1.8,
                                                   "h0": 0.2},
                         h_range=(0.5, 24.0), q_range=(1.0, 50000.0),
                         quality={"log_r2": 0.99, "n": 100})
    artifact = {
        "tick_minutes": 15,
        "required_history_ticks": 96,
        "variants": [],
        "ratings": {"B": rating.to_dict()},
    }
    return ModelBundle(artifact)


def _pred(**kw):
    fields = dict(role="B", ts=TS0, variant="A+T", inputs_used=("A", "T"),
                  logq_obs=2.0, logq_pred=2.0, scale=0.08, q_pred=7.389056,
                  stage_pred=5.0)
    fields.update(kw)
    return Prediction(**fields)


# AC-QI: the estimate is identical for different B readings — it derives
# only from the model prediction, never from B's observed value.
def test_estimate_identical_for_different_b_readings(settings, bundle):
    e1 = make_estimate(_pred(logq_obs=2.0), bundle, True, settings)
    e2 = make_estimate(_pred(logq_obs=3.0), bundle, True, settings)
    assert e1 == e2


def test_estimate_interval_is_rating_inverse_of_band(settings, bundle):
    e = make_estimate(_pred(), bundle, True, settings)
    band_z = settings.threshold("context", "band_z")
    scale = 0.08
    assert e.stage_lo is not None and e.stage_hi is not None
    assert e.stage_lo < e.stage_hat < e.stage_hi
    q = bundle.q_from_stage("B", e.stage_hat)
    assert abs(q - 7.389056) < 1e-4          # exp(2.0)
    lo_q = bundle.q_from_stage("B", e.stage_lo)
    hi_q = bundle.q_from_stage("B", e.stage_hi)
    import math
    assert abs(math.log(lo_q) - (2.0 - band_z * scale)) < 1e-9
    assert abs(math.log(hi_q) - (2.0 + band_z * scale)) < 1e-9


def test_estimate_confidence_by_variant(settings, bundle):
    assert make_estimate(_pred(variant="A+T+R", inputs_used=("A", "T", "R")),
                         bundle, True, settings).confidence is Conf.HIGH
    assert make_estimate(_pred(variant="A+T"), bundle, True,
                         settings).confidence is Conf.HIGH
    assert make_estimate(_pred(variant="A+R", inputs_used=("A", "R")),
                         bundle, True, settings).confidence is Conf.MED
    assert make_estimate(_pred(variant="T", inputs_used=("T",)),
                         bundle, True, settings).confidence is Conf.MED
    assert make_estimate(_pred(variant="R", inputs_used=("R",)),
                         bundle, True, settings).confidence is Conf.LOW


def test_estimate_error_prediction_is_none_confidence(settings, bundle):
    e = make_estimate(_pred(variant="none", error="no variant available"),
                      bundle, True, settings)
    assert e.confidence is Conf.NONE
    assert e.stage_hat is None and e.q_hat is None


def test_estimate_in_use_passthrough(settings, bundle):
    assert make_estimate(_pred(), bundle, True, settings).in_use is True
    assert make_estimate(_pred(), bundle, False, settings).in_use is False


def test_confidence_for_table():
    assert confidence_for("A+T") is Conf.HIGH
    assert confidence_for("A+T+R") is Conf.HIGH
    assert confidence_for("A+R") is Conf.MED
    assert confidence_for("T") is Conf.MED
    assert confidence_for("R") is Conf.LOW
    assert confidence_for("none") is Conf.NONE
