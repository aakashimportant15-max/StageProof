"""T018-T020 acceptance: synthetic-window tests for every RULES 3.2 check,
context helpers (3.4-3.6, 3.9-3.10), and replay matching (3.8). Threshold
values come from config/thresholds.yaml (RULES 5)."""

from datetime import datetime, timedelta

import numpy as np

from stageproof.checks import (HealthResult, ReplayArchive, drift_measure,
                               health_checks, is_notable, rain_support,
                               upstream_trend)
from stageproof.domain import Support, Trend, Window
from stageproof.settings import (DriftThresholds, HealthThresholds,
                                 RainThresholds, ReplayThresholds,
                                 TrendThresholds)

TS0 = datetime(2020, 3, 1)

HEALTH = HealthThresholds(
    dropout_ticks=2, stuck_ticks=8, stuck_pred_change_min=0.05,
    spike_revert_ticks=3, spike_revert_tol_steps=3, noise_window_ticks=8,
    noise_ratio_max=5.0, clean_window_ticks=16, clean_ratio_min=0.2,
    clean_min_move_steps=3,
)
TREND = TrendThresholds(up_window_ticks=8, up_rising_min=0.10, up_flat_max=0.03)
RAIN = RainThresholds(window_hours=48, yes_mm=None, no_fraction=0.25,
                      no_mm=None, stale_hours=3)
DRIFT = DriftThresholds(max_dev=0.25, max_rate_per_hour=0.02)
REPLAY = ReplayThresholds(window_ticks=24, tol_steps=2, min_range_steps=5,
                          min_age_days=7)

PARAMS = {"quant_step": 0.01, "spike_delta_max": 0.26,
          "noise_baseline_std": 0.008}
SMIN, SMAX = 2.0, 30.0


def _window(stage=None, logq=None, missing=None, trust=None, rain=(),
            rain_ok=False):
    stage = {k: tuple(v) for k, v in (stage or {}).items()}
    n = max((len(v) for v in stage.values()), default=1)
    return Window(
        ts=TS0 + timedelta(minutes=15 * (n - 1)), tick_idx=n - 1,
        stage=stage, logq={k: tuple(v) for k, v in (logq or {}).items()},
        missing=missing or {}, trust=trust or {}, rain_hourly=tuple(rain),
        rain_acc={}, rain_available=rain_ok,
    )


def _health(role="B", series=(), missing=False, trust=True,
            pred_change=None, params=None):
    window = _window(stage={role: series},
                     missing={role: missing}, trust={role: trust})
    return health_checks(role, window, HEALTH, params or PARAMS,
                         SMIN, SMAX, pred_change=pred_change)


# ---------------------------------------------------------------------------
# T018 — DROPOUT / RANGE
# ---------------------------------------------------------------------------

def test_dropout_at_threshold_and_below():
    ok = [5.0] * 10
    r2 = _health(series=ok + [np.nan, np.nan], missing=True)
    assert "DROPOUT" in r2.flags and r2.shape_ok is False
    r1 = _health(series=ok + [np.nan], missing=True)
    assert "DROPOUT" not in r1.flags and r1.shape_ok is True


def test_dropout_empty_history():
    r = _health(series=())
    assert r.flags == ("DROPOUT",) and r.shape_ok is False


def test_range_breach_and_nonfinite_with_data():
    r = _health(series=[5.0] * 5 + [35.0])
    assert "RANGE" in r.flags and r.shape_ok is False
    r_nan = _health(series=[5.0] * 5 + [np.nan], missing=False)
    assert "RANGE" in r_nan.flags
    r_missing = _health(series=[5.0] * 5 + [np.nan], missing=True)
    assert "RANGE" not in r_missing.flags


# ---------------------------------------------------------------------------
# T018 — SPIKE retroactive / RATE_EXCEEDED (N-10)
# ---------------------------------------------------------------------------

def test_spike_retroactive_on_revert():
    series = [5.0] * 19 + [5.5, 5.0]
    r = _health(series=series)
    assert "SPIKE" in r.flags and r.shape_ok is False
    assert r.rate_exceeded is False


def test_flash_flood_rise_is_not_a_fault():
    # N-10: three consecutive >spike_max rises that never revert are a rise,
    # not a fault: rate_exceeded only, shape stays ok. Large noise baseline
    # so the genuine rise does not also trip the NOISE check.
    series = [5.0] * 18 + [5.3, 5.6, 5.9]
    r = _health(series=series,
                params={**PARAMS, "noise_baseline_std": 0.5})
    assert "SPIKE" not in r.flags
    assert r.rate_exceeded is True
    assert r.shape_ok is True


def test_spike_without_revert_within_tolerance_is_rate_only():
    series = [5.0] * 20 + [5.5]
    r = _health(series=series,
                params={**PARAMS, "noise_baseline_std": 0.5})
    assert "SPIKE" not in r.flags
    assert r.rate_exceeded is True and r.shape_ok is True


# ---------------------------------------------------------------------------
# T018 — STUCK / NOISE / NOISE_TOO_CLEAN (B-only checks)
# ---------------------------------------------------------------------------

def test_stuck_needs_flat_and_moving_prediction():
    flat = [5.0] * 10
    r = _health(series=flat, pred_change=0.30)
    assert "STUCK" in r.flags and r.shape_ok is False
    r_quiet = _health(series=flat, pred_change=0.04)
    assert "STUCK" not in r_quiet.flags
    moving = [5.0, 5.02] * 5
    r_range = _health(series=moving, pred_change=0.30)
    assert "STUCK" not in r_range.flags


def test_stuck_is_target_only():
    r = _health(role="A", series=[5.0] * 10, pred_change=0.30)
    assert "STUCK" not in r.flags


def test_noise_burst_flagged_and_quiet_not():
    burst = [5.0, 5.2] * 6
    r = _health(series=burst)
    assert "NOISE" in r.flags and r.shape_ok is False
    quiet = list(5.0 + 0.002 * np.sin(np.arange(20)))
    r_ok = _health(series=quiet)
    assert "NOISE" not in r_ok.flags and r_ok.shape_ok is True


def test_noise_is_target_only():
    r = _health(role="A", series=[5.0, 5.2] * 6)
    assert "NOISE" not in r.flags and r.shape_ok is True


def test_noise_too_clean_ramp():
    ramp = [4.0 + 0.05 * i for i in range(20)]
    r = _health(series=ramp)
    assert r.noise_too_clean is True
    assert r.shape_ok is True  # booster only, never a fault by itself


def test_noisy_ramp_not_too_clean():
    ramp = [4.0 + 0.05 * i + (0.05 if i % 2 else -0.05) for i in range(20)]
    r = _health(series=ramp)
    assert r.noise_too_clean is False


def test_health_result_defaults():
    r = HealthResult(role="B")
    assert r.flags == () and r.shape_ok is True and r.rate_exceeded is False


# ---------------------------------------------------------------------------
# T019 — upstream trend (RULES 3.4)
# ---------------------------------------------------------------------------

def _trend_logq(delta_per_tick, n=20):
    return [0.0 + delta_per_tick * i for i in range(n)]


def test_trend_rising_falling_flat():
    rising = _window(logq={"A": _trend_logq(0.02)},
                     trust={"A": True})
    assert upstream_trend(rising, TREND) == Trend.RISING
    falling = _window(logq={"A": _trend_logq(-0.00625)},
                      trust={"A": True})
    assert upstream_trend(falling, TREND) == Trend.FALLING
    flat = _window(logq={"A": _trend_logq(0.0025)}, trust={"A": True})
    assert upstream_trend(flat, TREND) == Trend.FLAT


def test_trend_trust_and_fallback_order():
    a_dead = _window(logq={"A": _trend_logq(0.02), "T": _trend_logq(-0.01)},
                     trust={"A": False, "T": True})
    assert upstream_trend(a_dead, TREND) == Trend.FALLING
    both_dead = _window(logq={"A": _trend_logq(0.02), "T": _trend_logq(0.02)},
                        trust={"A": False, "T": False})
    assert upstream_trend(both_dead, TREND) == Trend.UNKNOWN


def test_trend_short_or_nan_window_unknown():
    short = _window(logq={"A": _trend_logq(0.02, n=8)}, trust={"A": True})
    assert upstream_trend(short, TREND) == Trend.UNKNOWN
    hole = _window(logq={"A": _trend_logq(0.02)}, trust={"A": True})
    hole.logq["A"] = tuple([np.nan] * 20)
    assert upstream_trend(hole, TREND) == Trend.UNKNOWN


# ---------------------------------------------------------------------------
# T019 — rain support (RULES 3.6)
# ---------------------------------------------------------------------------

def test_rain_yes_no_unknown_bands():
    yes = _window(rain=[0.06] * 48, rain_ok=True)
    assert rain_support(yes, RAIN, 2.0, 0.5) == Support.YES
    no = _window(rain=[0.005] * 48, rain_ok=True)
    assert rain_support(no, RAIN, 2.0, 0.5) == Support.NO
    mid = _window(rain=[0.03] * 48, rain_ok=True)
    assert rain_support(mid, RAIN, 2.0, 0.5) == Support.UNKNOWN


def test_rain_insufficient_history_or_feed():
    short = _window(rain=[0.06] * 47, rain_ok=True)
    assert rain_support(short, RAIN, 2.0, 0.5) == Support.UNKNOWN
    dead = _window(rain=[0.06] * 48, rain_ok=False)
    assert rain_support(dead, RAIN, 2.0, 0.5) == Support.UNKNOWN
    nothr = _window(rain=[0.06] * 48, rain_ok=True)
    assert rain_support(nothr, RAIN, None, 0.5) == Support.UNKNOWN


def test_rain_staleness_and_partial_nan():
    stale = _window(rain=[0.06] * 44 + [np.nan] * 4, rain_ok=True)
    assert rain_support(stale, RAIN, 2.0, 0.5) == Support.UNKNOWN
    recent_gap = _window(rain=[0.06] * 46 + [np.nan] * 2, rain_ok=True)
    assert rain_support(recent_gap, RAIN, 2.0, 0.5) == Support.YES


# ---------------------------------------------------------------------------
# T019 — drift measure (RULES 3.9) and notable (3.10)
# ---------------------------------------------------------------------------

def test_drift_small_stationary_residual():
    assert drift_measure([0.02] * 6, 15, DRIFT, window_ticks=6) is True


def test_drift_rejects_big_deviation_and_fast_rate():
    assert drift_measure([0.5] * 6, 15, DRIFT, window_ticks=6) is False
    # mean 0.1 < max_dev but early->late jump is too fast: boundary excluded
    fast = [0.0, 0.0, 0.0, 0.2, 0.2, 0.2]
    assert drift_measure(fast, 15, DRIFT, window_ticks=6) is False
    slow = [0.0, 0.005, 0.01, 0.015, 0.02, 0.025]
    assert drift_measure(slow, 15, DRIFT, window_ticks=6) is True


def test_drift_needs_two_points():
    assert drift_measure([0.01], 15, DRIFT, window_ticks=6) is False
    assert drift_measure([], 15, DRIFT, window_ticks=6) is False


def test_is_notable():
    assert is_notable(14.0, 13.0, 14.0) is True
    assert is_notable(13.9, None, 14.0) is False
    assert is_notable(None, 14.1, 14.0) is True
    assert is_notable(None, None, 14.0) is False


# ---------------------------------------------------------------------------
# T020 — replay archive (RULES 3.8)
# ---------------------------------------------------------------------------

def _archive_with_bumps():
    ts = [TS0 + timedelta(hours=h) for h in range(400)]
    stage = [10.0] * 400
    bump = [10.0 + 0.4 * np.sin(np.pi * j / 23) for j in range(24)]
    for off in (20, 100):          # end on day 1.8 and 5.1 (both < cutoff 9.7)
        for j in range(24):
            stage[off + j] = bump[j]
    for j in range(24):            # ends day 15.6 -> excluded by min_age_days
        stage[350 + j] = 99.0 + j * 0.001
    archive = ReplayArchive(REPLAY, quant_step=0.01)
    stored = archive.add_history(stage, ts, live_start=ts[399] + timedelta(hours=1))
    # Every eligible window (not just the bumps) is archived.
    assert stored >= 2
    return archive, bump


def test_replay_exact_copy_flagged_different_flood_not():
    archive, bump = _archive_with_bumps()
    assert archive.match(bump) is True
    shifted = [v + 0.05 for v in bump]     # beyond tol_steps=2 * 0.01
    assert archive.match(shifted) is False
    other = [10.0 + 0.4 * (j / 23.0) for j in range(24)]   # different shape
    assert archive.match(other) is False
    assert archive.match(bump[:23]) is False   # short window


def test_replay_recent_window_not_archived():
    archive, _ = _archive_with_bumps()
    recent_only = [99.0 + j * 0.001 for j in range(24)]
    assert archive.match(recent_only) is False
