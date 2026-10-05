"""EvidenceBuilder tests (T021; RULES §3.3-§3.6, §3.8-§3.10).

Uses the real config/thresholds.yaml values: z_consistent 3, z_implausible 5,
z_window_ticks 3, scale_floor 0.02, fault_attack_ticks 3 (drift window).
"""

from datetime import datetime, timedelta, timezone

import pytest

from stageproof.checks import HealthResult
from stageproof.domain import (Context, Downstream, Prediction, Support,
                               Trend, Window)
from stageproof.evidence import EvidenceBuilder
from stageproof.settings import load_settings

T0 = datetime(2020, 11, 12, tzinfo=timezone.utc)
WATCH = 14.0
YES_MM, NO_MM = 10.0, 2.5


@pytest.fixture(scope="module")
def thresholds():
    return load_settings("config", ".env").thresholds


def builder(th, lag_bc=4):
    return EvidenceBuilder(th, WATCH, 15, lag_bc, YES_MM, NO_MM)


def win(tick, trust=None, stage=None, logq=None, rain=None):
    return Window(
        ts=T0 + timedelta(minutes=15 * tick),
        tick_idx=tick,
        stage=stage or {},
        logq=logq or {},
        missing={},
        trust=({"A": True, "T": True, "B": True, "C": True}
               if trust is None else trust),
        rain_hourly=tuple(rain or ()),
        rain_available=rain is not None,
    )


def pred(inputs=("A", "T"), obs=2.0, p=2.0, scale=0.05, stage_pred=5.0,
         role="B", **kw):
    return Prediction(role=role, ts=T0, variant="+".join(inputs),
                      inputs_used=tuple(inputs), logq_obs=obs, logq_pred=p,
                      scale=scale, stage_pred=stage_pred, **kw)


def feed(b, w, p=None, d=None, tflags=None, thard=None, health=None, **kw):
    return b.build(w, p, d, tflags or {}, thard or {}, health or {}, **kw)


OK_HEALTH = {"B": HealthResult(role="B", flags=(), shape_ok=True)}


def test_insufficient_when_no_prediction(thresholds):
    ev = feed(builder(thresholds), win(0))
    assert ev.context == Context.INSUFFICIENT
    assert ev.z is None and ev.z_mean is None
    assert ev.obs_stage is None and not ev.notable


def test_insufficient_on_prediction_error(thresholds):
    p = pred(error="model unavailable")
    ev = feed(builder(thresholds), win(0), p, health=OK_HEALTH)
    assert ev.context == Context.INSUFFICIENT


def test_insufficient_for_rain_only_inputs(thresholds):
    p = pred(inputs=("R",), obs=2.0, p=1.95, scale=0.05)
    ev = feed(builder(thresholds), win(0), p, health=OK_HEALTH)
    assert ev.context == Context.INSUFFICIENT


def test_consistent_tick_full_fields(thresholds):
    b = builder(thresholds)
    w = win(0, stage={"B": (10.0, 10.01)}, logq={"A": tuple([2.0] * 9)},
            rain=(0.0,) * 48)
    p = pred(inputs=("A", "T"), obs=2.0, p=1.95, scale=0.05)
    ev = feed(b, w, p, tflags={"A": ("TS_SKEW",)}, health=OK_HEALTH)
    assert ev.context == Context.CONSISTENT
    assert ev.z == pytest.approx(1.0)
    assert ev.z_mean == pytest.approx(1.0)
    assert ev.variant == "A+T"
    assert ev.up_trend == Trend.FLAT
    assert ev.rain == Support.NO
    assert ev.downstream == Downstream.UNKNOWN
    assert ev.shape_ok is True
    assert ev.rate_exceeded is False
    assert ev.noise_too_clean is False
    assert ev.trusted_inputs == {"A": True, "T": True, "B": True, "C": True}
    assert ev.transport_flags == {"A": ("TS_SKEW",)}
    assert ev.obs_stage == pytest.approx(10.01)
    assert ev.pred_stage == pytest.approx(5.0)
    assert ev.notable is False
    assert ev.degraded is False


def test_z_floored_by_scale_floor(thresholds):
    b = builder(thresholds)
    p = pred(obs=2.0, p=1.95, scale=0.001)
    ev = feed(b, win(0), p, health=OK_HEALTH)
    assert ev.z == pytest.approx(2.5)
    assert ev.context == Context.CONSISTENT


def test_z_mean_uses_last_window_only(thresholds):
    b = builder(thresholds)
    for t in range(3):
        ev = feed(b, win(t), pred(obs=2.0, p=1.95, scale=0.001))
    assert ev.z_mean == pytest.approx(2.5)
    ev = feed(b, win(3), pred(obs=1.95, p=2.0, scale=0.001))
    assert ev.z_mean == pytest.approx((2.5 + 2.5 - 2.5) / 3)


def test_phantom_constant_residual_is_drift(thresholds):
    # resid 0.11 lnQ at scale floor 0.02 -> z 5.5 (PHANTOM), |mean| < 0.25,
    # zero rate -> RULES §3.9 drift condition met.
    b = builder(thresholds)
    ev = None
    for t in range(3):
        ev = feed(b, win(t), pred(obs=2.0, p=1.89, scale=0.02))
        assert ev.context == Context.PHANTOM
    assert ev.drift is True


def test_phantom_large_residual_is_not_drift(thresholds):
    b = builder(thresholds)
    for t in range(3):
        ev = feed(b, win(t), pred(obs=2.5, p=2.0, scale=0.02))
    assert ev.context == Context.PHANTOM
    assert ev.drift is False


def test_suppressed_constant_residual_is_drift(thresholds):
    b = builder(thresholds)
    for t in range(3):
        ev = feed(b, win(t), pred(obs=1.89, p=2.0, scale=0.02))
    assert ev.context == Context.SUPPRESSED
    assert ev.drift is True


def test_ambiguous_context(thresholds):
    # |z_mean| between z_consistent and z_implausible with upstream inputs.
    b = builder(thresholds)
    ev = feed(b, win(0), pred(obs=2.0, p=1.93, scale=0.02))
    assert ev.z == pytest.approx(3.5)
    assert ev.context == Context.AMBIGUOUS


def test_notable_thresholds(thresholds):
    b = builder(thresholds)
    ev = feed(b, win(0, stage={"B": (13.99,)}), pred(stage_pred=13.0))
    assert ev.notable is False
    ev = feed(b, win(1, stage={"B": (14.0,)}), pred(stage_pred=13.0))
    assert ev.notable is True
    ev = feed(b, win(2, stage={"B": (10.0,)}), pred(stage_pred=15.0))
    assert ev.notable is True


def test_downstream_pending_until_lag_then_yes(thresholds):
    b = builder(thresholds, lag_bc=4)
    down = pred(obs=2.0, p=2.0, scale=0.05, role="C")
    states = []
    for t in range(5):
        w = win(t, stage={"B": (15.0,)})
        ev = feed(b, w, pred(stage_pred=5.0), d=down)
        states.append(ev.downstream)
    assert states == [Downstream.PENDING, Downstream.PENDING,
                      Downstream.PENDING, Downstream.PENDING,
                      Downstream.YES]
    assert ev.z_down_mean == pytest.approx(0.0)


def test_downstream_no_on_large_deviation(thresholds):
    b = builder(thresholds, lag_bc=4)
    down = pred(obs=1.5, p=2.0, scale=0.02, role="C")
    ev = None
    for t in range(5):
        w = win(t, stage={"B": (15.0,)})
        ev = feed(b, w, pred(), d=down)
    assert ev.downstream == Downstream.NO
    assert ev.z_down_mean <= -5.0


def test_downstream_unknown_when_c_untrusted(thresholds):
    b = builder(thresholds, lag_bc=4)
    down = pred(obs=2.0, p=2.0, scale=0.05, role="C")
    w = win(0, trust={"A": True, "T": True, "B": True, "C": False},
            stage={"B": (15.0,)})
    ev = feed(b, w, pred(), d=down)
    assert ev.downstream == Downstream.UNKNOWN


def test_downstream_unknown_without_prediction(thresholds):
    b = builder(thresholds, lag_bc=4)
    w = win(0, stage={"B": (15.0,)})
    ev = feed(b, w, pred())
    assert ev.downstream == Downstream.UNKNOWN


def test_health_flag_passthrough(thresholds):
    health = {"B": HealthResult(role="B", flags=("NOISE",), shape_ok=False,
                                rate_exceeded=True, noise_too_clean=True)}
    ev = feed(builder(thresholds), win(0), pred(), health=health,
              replay_match=True)
    assert ev.health_flags == {"B": ("NOISE",)}
    assert ev.shape_ok is False
    assert ev.rate_exceeded is True
    assert ev.noise_too_clean is True
    assert ev.replay_match is True


def test_degraded_passthrough(thresholds):
    p = pred(degraded=True)
    ev = feed(builder(thresholds), win(0), p, health=OK_HEALTH)
    assert ev.degraded is True
