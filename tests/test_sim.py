"""Sim-layer tests: injections (T034/T035), the signed stream (T036) and the
expectation evaluator (T037). Uses a synthetic in-memory dataset — no data
files needed. Sim must never import engine modules (static check below).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from stageproof.domain import (Action, ActionStatus, ActionType, AlertState,
                               Label, Scenario, SensorState, SensorStatus,
                               TickRecord, Verdict, dataclass_from_dict,
                               dataclass_to_dict)
from stageproof.security.transport import (TransportState, verify)
from stageproof.sim import corrupt
from stageproof.sim.scenarios import (ExpectationResult, ScenarioError,
                                      ScenarioStream, evaluate_expectations,
                                      load_manifest, save_manifest)

UTC = timezone.utc
T0 = datetime(2020, 11, 12, 6, 0, tzinfo=UTC)
KEYS = {"A": "ka", "T": "kt", "B": "kb", "C": "kc"}
STATIONS = {
    "A": {"quant_step": 0.01, "noise_std": 0.007, "sensor_min": 1.0,
          "sensor_max": 24.0},
    "T": {"quant_step": 0.01, "noise_std": 0.006, "sensor_min": 1.0,
          "sensor_max": 27.5},
    "B": {"quant_step": 0.01, "noise_std": 0.008, "sensor_min": 0.0,
          "sensor_max": 26.0},
    "C": {"quant_step": 0.01, "noise_std": 0.005, "sensor_min": 2.0,
          "sensor_max": 20.5},
}


def make_df(n=64, base=5.0, rise_from=40, rise=6.0):
    # history begins 2 h before the window so warmup_ticks has room
    idx = pd.date_range(T0 - timedelta(hours=2), periods=n + 8,
                        freq="15min", tz=UTC)
    full = np.full(n + 8, base)
    full[rise_from + 8:] = base + np.linspace(0, rise, n - rise_from)
    df = pd.DataFrame(index=idx)
    for r in ("A", "T", "B", "C"):
        df[f"{r}_stage"] = full + 0.5 * ("ATBC".index(r))
        df[f"{r}_q"] = 1000.0 * (full / base) ** 2
    df["rain_mm_prev_hr"] = 0.0
    df.loc[idx[16], "rain_mm_prev_hr"] = 4.2
    return df  # T0 sits at row 8; the window slice happens inside the stream


def make_manifest(n=64, **overrides):
    d = dict(
        id="TEST", title="t", description="d", seed=7,
        window={"start": T0.isoformat(),
                "end": (T0 + timedelta(minutes=15 * (n - 1))).isoformat()},
        warmup_ticks=4, event_start_tick=8,
        corruptions=(), compromised_keys=(), transport_attacks=(),
        feed_outages=(), volunteer_script=(), operator_script=(),
        ground_truth={"label": "REAL_FLOOD", "truth_notable": True},
        expected={},
    )
    d.update(overrides)
    return dataclass_from_dict(d, Scenario)


def stream_for(manifest, df):
    return ScenarioStream(manifest, df, STATIONS, KEYS)


# ---------------------------------------------------------------------------
# T034 — emulator and faults
# ---------------------------------------------------------------------------

def test_emulate_sensor_quantizes_and_is_seedable():
    rng = np.random.default_rng(42)
    out = corrupt.emulate_sensor(np.full(500, 7.0), rng, 0.008, 0.01)
    assert np.allclose(out, np.round(out, 2))
    assert 0.001 < out.std() < 0.03
    out2 = corrupt.emulate_sensor(np.full(500, 7.0),
                                  np.random.default_rng(42), 0.008, 0.01)
    assert np.array_equal(out, out2)


def test_dropout_range():
    s = np.arange(10, dtype=float)
    out = corrupt.apply_dropout(s, 3, 6)
    assert np.isnan(out[3:7]).all() and np.isfinite(out[:3]).all()
    assert np.array_equal(out[7:], s[7:])


def test_stuck_freezes_at_last_good_value():
    s = np.array([5.0, 5.01, 5.02, 5.03, 5.04, 5.05, 5.06])
    out = corrupt.apply_stuck(s, 3, 6)
    # the sensor freezes at the value reported when it stuck (tick 3)
    assert np.all(out[3:] == 5.03) and out[2] == 5.02


def test_spike_is_one_tick_and_reverts():
    s = np.zeros(8)
    out = corrupt.apply_spike(s, 4, 1.5)
    assert out[4] == 1.5 and out[3] == 0.0 and out[5] == 0.0


def test_noise_burst_inflates_diff_std():
    s = np.full(40, 5.0)
    out = corrupt.apply_noise_burst(s, 10, 29, np.random.default_rng(1),
                                    20.0, 0.008)
    burst = np.diff(out[10:30]).std()
    quiet = np.diff(out[:10]).std()
    assert burst > 10 * max(quiet, 1e-6)


def test_drift_is_slow_linear_offset():
    s = np.zeros(20)
    out = corrupt.apply_drift(s, 5, 15, 0.3)
    assert out[5] == 0.0 and abs(out[15] - 0.3) < 1e-9
    assert out[10] == pytest.approx(0.15, abs=1e-9)
    assert out[16] == 0.0 and out[4] == 0.0


def test_range_oob_pins_outside_envelope():
    s = np.full(10, 5.0)
    out = corrupt.apply_range_oob(s, 2, 5, 0.0, 26.0)
    assert np.all(out[2:6] == 26.5) and out[1] == 5.0 and out[6] == 5.0
    low = corrupt.apply_range_oob(s, 2, 5, 0.0, 26.0, high=False)
    assert np.all(low[2:6] == -0.5)


def test_fabricated_ramp_shape_and_rate_cap():
    n = 200
    s = np.full(n, 5.0)
    out = corrupt.apply_fabricated_ramp(s, 20, 15.0, 24, 8, 32,
                                        max_per_tick=0.3)
    assert out[19] == 5.0
    assert out.max() == pytest.approx(15.0, abs=1e-9)
    # decline's first point duplicates the peak, so the plateau is hold+1
    assert np.sum(out == out.max()) >= 8
    assert out[-1] == pytest.approx(5.0, abs=1e-9)
    deltas = np.abs(np.diff(out))
    assert deltas.max() <= 0.3 + 1e-9


def test_fabricated_ramp_stretches_when_capped():
    s = np.full(100, 4.0)
    out = corrupt.apply_fabricated_ramp(s, 10, 14.0, 5, 2, 5,
                                        max_per_tick=0.2)
    assert np.all(np.diff(out[10:15]) > 0)  # strictly rising from tick 10
    assert np.abs(np.diff(out)).max() <= 0.2 + 1e-9
    assert out[14] < 14.0  # rise stretched past the requested span
    assert out.max() == pytest.approx(14.0, abs=1e-9)


def test_suppression_blends_toward_baseline():
    s = np.concatenate([np.full(10, 5.0), np.full(10, 15.0)])
    out = corrupt.apply_suppression(s, 10, 19, baseline=5.0, strength=0.85)
    assert np.allclose(out[10:], 5.0 + 0.15 * 10.0)
    assert np.array_equal(out[:10], s[:10])


def test_unsigned_inject_and_replay_helpers():
    from stageproof.domain import SensorReading
    r = SensorReading("B", T0, 3, 7.0, "ft", "sig")
    m = corrupt.unsigned_inject(r, 22.0)
    assert m.stage == 22.0 and m.sig == "sig" and m.seq == 3
    hist = [SensorReading("B", T0, i + 1, 1.0, "ft", f"s{i}")
            for i in range(6)]
    old = corrupt.replay_packet(hist, 4)
    assert old.seq == 2
    assert corrupt.replay_packet([], 4) is None


# ---------------------------------------------------------------------------
# T036 — manifest IO and the signed stream
# ---------------------------------------------------------------------------

def test_manifest_round_trip(tmp_path):
    m = make_manifest(corruptions=({"role": "B", "type": "STUCK",
                                    "start_tick": 20, "end_tick": 40,
                                    "params": {}},),
                      compromised_keys=("B",))
    p = tmp_path / "m.json"
    save_manifest(m, p)
    m2 = load_manifest(p)
    assert m2 == m and m2.corruptions[0]["type"] == "STUCK"


def test_manifest_rejects_unknown_types():
    bad = make_manifest(corruptions=({"role": "B", "type": "MELT",
                                      "start_tick": 1, "end_tick": 2,
                                      "params": {}},))
    with pytest.raises(ScenarioError):
        ScenarioStream(bad, make_df(), STATIONS, KEYS)


def test_stream_is_deterministic_for_same_seed():
    df, m = make_df(), make_manifest()
    a = list(stream_for(m, df))
    b = list(stream_for(m, df))
    assert len(a) == len(b) == 64 + m.warmup_ticks
    for (ti, tv), (ti2, tv2) in zip(a, b):
        assert ti == ti2 and tv == tv2
        assert all(r.sig == r2.sig for r, r2
                   in zip(ti.readings.values(), ti2.readings.values())
                   if r is not None and r2 is not None)


def test_stream_length_and_warmup_start():
    m = make_manifest()
    st = stream_for(m, make_df())
    ti, _ = st.tick(0)
    assert len(st) == 64 + m.warmup_ticks  # window incl. warmup rows
    assert ti.tick_idx == 0
    assert ti.ts == T0 - timedelta(minutes=15 * m.warmup_ticks)


def test_packets_pass_transport_when_clean():
    df, m = make_df(), make_manifest()
    st = stream_for(m, df)
    states = {r: TransportState(role=r) for r in "ATBC"}
    for idx in range(20):
        ti, _ = st.tick(idx)
        for r in "ATBC":
            v = verify(states[r], ti.readings[r], r, ti.ts, KEYS, 300.0)
            assert v.accepted, (idx, r, v.flags)


def test_compromised_key_packets_are_signature_valid():
    m = make_manifest(
        corruptions=({"role": "B", "type": "FABRICATED_RAMP",
                      "start_tick": 16, "end_tick": 60,
                      "params": {"peak_stage": 18.0, "rise_ticks": 24,
                                 "hold_ticks": 8, "decline_ticks": 12}},),
        compromised_keys=("B",))
    st = stream_for(m, make_df())
    state = TransportState(role="B")
    for idx in range(16, 50):
        ti, _ = st.tick(idx)
        v = verify(state, ti.readings["B"], "B", ti.ts, KEYS, 300.0)
        assert v.accepted, (idx, v.flags)
        if idx >= 20:  # ramp offset is 0 at 16; rising by 20
            assert ti.readings["B"].stage > 6.0  # the LIE is in the value


def test_unsigned_inject_fails_signature():
    m = make_manifest(transport_attacks=(
        {"type": "UNSIGNED_INJECT", "role": "B", "tick": 10,
         "params": {"stage": 22.0}},))
    st = stream_for(m, make_df())
    ti, _ = st.tick(10)
    v = verify(TransportState(role="B"), ti.readings["B"], "B", ti.ts,
               KEYS, 300.0)
    assert not v.accepted and "SIG_INVALID" in v.hard


def test_replay_packet_reuses_sequence():
    m = make_manifest(transport_attacks=(
        {"type": "REPLAY_PACKET", "role": "B", "tick": 10,
         "params": {"from_offset_ticks": 4}},))
    st = stream_for(m, make_df())
    state = TransportState(role="B")
    for idx in range(11):  # the attack fires AT tick 10
        ti, _ = st.tick(idx)
        v = verify(state, ti.readings["B"], "B", ti.ts, KEYS, 300.0)
    assert not v.accepted and "SEQ_REPLAY" in v.hard


def test_feed_outages_blank_channels_and_rain():
    m = make_manifest(feed_outages=({"feed": "A", "start_tick": 10,
                                     "end_tick": 20},
                                    {"feed": "RAIN", "start_tick": 12,
                                     "end_tick": 14}))
    st = stream_for(m, make_df())
    ti, _ = st.tick(13)
    assert ti.readings["A"] is None and ti.readings["B"] is not None
    assert ti.rain_feed_ok is False and ti.rain_prev_hr_mm is None
    ti9, _ = st.tick(9)
    assert ti9.readings["A"] is not None and ti9.rain_feed_ok is True


def test_truth_separate_and_labeled_by_event_start():
    df, m = make_df(), make_manifest()
    st = stream_for(m, df)
    ti_pre, tv_pre = st.tick(7)
    ti_post, tv_post = st.tick(9)
    assert tv_pre.truth_label == "NORMAL" and not tv_pre.truth_notable
    assert tv_post.truth_label == "REAL_FLOOD" and tv_post.truth_notable
    assert set(tv_post.true_stage) == {"A", "T", "B", "C"}
    assert tv_post.true_q_B == pytest.approx(st.df["B_q"].iloc[9])
    assert tv_post.true_stage["B"] == pytest.approx(st.df["B_stage"].iloc[9])
    assert tv_post.corrupted_roles == ()
    # no truth fields on the engine-facing object
    import dataclasses
    tfields = {f.name for f in dataclasses.fields(type(ti_post))}
    assert not tfields & {"truth_label", "true_q_B", "true_stage",
                          "corrupted_roles", "truth_notable"}


def test_corrupted_roles_reported_per_tick():
    m = make_manifest(corruptions=({"role": "B", "type": "STUCK",
                                    "start_tick": 20, "end_tick": 30,
                                    "params": {}},))
    st = stream_for(m, make_df())
    assert st.tick(19)[1].corrupted_roles == ()
    assert st.tick(25)[1].corrupted_roles == ("B",)
    assert st.tick(31)[1].corrupted_roles == ()


def test_scripts_exposed_by_tick_offset():
    m = make_manifest(volunteer_script=({"tick_offset": 5,
                                         "volunteer_id": "V1", "code": 1},
                                        {"tick_offset": 7,
                                         "volunteer_id": "V2", "code": 1}),
                      operator_script=({"tick_offset": 9,
                                        "action": "approve"},))
    st = stream_for(m, make_df())
    assert st.volunteer_replies_at(8 + 5)[0]["volunteer_id"] == "V1"
    assert st.volunteer_replies_at(8 + 7)[0]["code"] == 1
    assert st.volunteer_replies_at(8 + 6) == []
    assert st.operator_events_at(8 + 9)[0]["action"] == "approve"


def test_sim_never_imports_engine_modules():
    import stageproof.sim.corrupt as c
    import stageproof.sim.scenarios as s
    for mod in (c, s):
        src = open(mod.__file__, encoding="utf-8").read()
        for engine in ("pipeline", "evidence", "decision", "checks",
                       "models", "estimator", "response", "community"):
            assert f"from ..{engine}" not in src, (mod.__name__, engine)
            assert f"from stageproof.{engine}" not in src, (mod.__name__,
                                                            engine)
            assert f"import stageproof.{engine}" not in src, (mod.__name__,
                                                              engine)


# ---------------------------------------------------------------------------
# T037 — expectation evaluator on handcrafted records
# ---------------------------------------------------------------------------

def make_records(n=30, commit_from=12, quarant_from=10,
                 provisional_from=15, threshold_alarm_at=14):
    records = []
    for i in range(n):
        label = Label.REAL_FLOOD if i >= commit_from else Label.NORMAL
        alert = (AlertState.PROVISIONAL_WARNING if i >= provisional_from
                 else AlertState.WATCH)
        sensor = ({"B": SensorStatus(role="B", state=SensorState.QUARANTINED,
                                     since_tick=quarant_from)}
                  if i >= quarant_from else {})
        actions = ((Action(action_id=f"a{i}", ts=T0, tier=1,
                           type=ActionType.QUARANTINE_SENSOR,
                           status=ActionStatus.EXECUTED, target="B"),)
                   if i == quarant_from else ())
        baselines = {"rollz_flag": 5 <= i < 10,
                     "rollz_value": 6.5 if 5 <= i < 10 else 1.0,
                     "threshold_alert": i == threshold_alarm_at}
        records.append(TickRecord(
            tick_idx=i, ts=T0 + timedelta(minutes=15 * i),
            verdict=Verdict(ts=T0 + timedelta(minutes=15 * i), tick_idx=i,
                            candidate=label, label=label),
            alert_state=alert,
            alert_source="OBSERVED" if i >= provisional_from else None,
            sensor_status=sensor, new_actions=actions,
            baselines=baselines))
    return records


def test_expectations_pass_and_fail():
    m = make_manifest(expected={
        "committed": ({"label": "REAL_FLOOD", "from_tick": 12, "to_tick": 20,
                       "tolerance_ticks": 2},
                      {"label": "SENSOR_FAULT", "from_tick": 0, "to_tick": 5,
                       "tolerance_ticks": 0}),
        "sensor": ({"role": "B", "state": "QUARANTINED", "by_tick": 15},
                   {"role": "B", "state": "RECOVERING", "by_tick": 29}),
        "alert": {"must_reach": "PROVISIONAL_WARNING", "source": "OBSERVED",
                  "within_ticks": 20,
                  "must_not_exceed": "WATCH"},
        "actions": {"must_include": ["QUARANTINE_SENSOR"],
                    "must_not_include": ["SEND_PUBLIC_WARNING"]},
        "baselines": {"rollz_flags_min": 3,
                      "threshold_alarm": "expected_true"},
    })
    results = evaluate_expectations(m, make_records())
    by_id = {r.id: r for r in results}
    assert all(isinstance(r, ExpectationResult) for r in results)
    assert by_id["committed[0]:REAL_FLOOD"].passed
    assert not by_id["committed[1]:SENSOR_FAULT"].passed
    assert by_id["sensor[0]:B=QUARANTINED"].passed
    assert not by_id["sensor[1]:B=RECOVERING"].passed
    assert by_id["alert:reaches:PROVISIONAL_WARNING(OBSERVED)"].passed
    assert not by_id["alert:never_above:WATCH"].passed
    assert by_id["action:includes:QUARANTINE_SENSOR"].passed
    assert by_id["action:excludes:SEND_PUBLIC_WARNING"].passed
    assert by_id["baselines:rollz_flags_min"].passed
    assert by_id["baselines:threshold_alarm:expected_true"].passed


def test_expectations_threshold_alarm_false():
    m = make_manifest(expected={"baselines":
                                {"threshold_alarm": "expected_false"}})
    results = evaluate_expectations(
        m, make_records(n=30, threshold_alarm_at=None))
    assert results[0].passed


def test_expectations_committed_tolerance_window():
    m = make_manifest(expected={
        "committed": ({"label": "REAL_FLOOD", "from_tick": 10, "to_tick": 11,
                       "tolerance_ticks": 2},)})
    results = evaluate_expectations(m, make_records())
    assert results[0].passed  # commit at 12 is inside 10..11 + tol 2
    assert "committed at tick 12" in results[0].detail
