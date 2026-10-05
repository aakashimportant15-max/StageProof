"""Decision engine tests (T022-T024; RULES §4 table + post-adjustments, §6
persistence/hysteresis)."""

from datetime import datetime, timezone

import pytest

from stageproof.decision import Candidate, VerdictTracker, decide
from stageproof.domain import (Conf, Context, Downstream, Evidence, Label,
                               SensorState, Support, Trend)
from stageproof.settings import load_settings

T0 = datetime(2020, 11, 12, tzinfo=timezone.utc)


def _ev(**kw):
    base = dict(
        ts=T0, tick_idx=0,
        transport_flags={}, transport_hard={}, health_flags={},
        shape_ok=True, rate_exceeded=False,
        variant="A+T", z=0.0, z_mean=0.0,
        context=Context.CONSISTENT,
        up_trend=Trend.FLAT, rain=Support.NO,
        downstream=Downstream.UNKNOWN,
        replay_match=False, noise_too_clean=False, drift=False,
        obs_stage=5.0, pred_stage=5.0, notable=False,
        degraded=False,
        trusted_inputs={"A": True, "T": True, "B": True, "C": True},
    )
    base.update(kw)
    return Evidence(**base)


@pytest.fixture(scope="module")
def persistence():
    return load_settings("config", ".env").thresholds.persistence


def tracker(persistence):
    return VerdictTracker(persistence)


# -- R1: hard transport flag on B -------------------------------------------

def test_r1_hard_flag_high_and_immediate_subtype():
    cand = decide(_ev(transport_hard={"B": ("SIG_INVALID",)}),
                  SensorState.TRUSTED)
    assert cand.label == Label.POSSIBLE_CYBER_ATTACK
    assert cand.subtype == "SIG_INVALID"
    assert cand.confidence == Conf.HIGH
    assert cand.rule_id == "R1"
    assert cand.reasons == ("TRANSPORT_SIG_INVALID",)


def test_r1_first_flag_wins_subtype():
    cand = decide(_ev(transport_hard={"B": ("SEQ_REPLAY", "STATION_MISMATCH")}),
                  SensorState.TRUSTED)
    assert cand.subtype == "SEQ_REPLAY"
    assert cand.reasons == ("TRANSPORT_SEQ_REPLAY",
                            "TRANSPORT_STATION_MISMATCH")


def test_r1_non_immediate_hard_flag_is_med():
    cand = decide(_ev(transport_hard={"B": ("TS_SKEW",)}),
                  SensorState.TRUSTED)
    assert cand.subtype == "TS_SKEW"
    assert cand.confidence == Conf.MED


def test_r1_immediate_flag_wins_over_leading_soft_order():
    cand = decide(_ev(transport_hard={"B": ("TS_SKEW", "SEQ_REPLAY",
                                             "TS_NONMONOTONIC")}),
                  SensorState.TRUSTED)
    assert cand.subtype == "SEQ_REPLAY"
    assert cand.confidence == Conf.HIGH
    assert cand.reasons == ("TRANSPORT_TS_SKEW", "TRANSPORT_SEQ_REPLAY",
                            "TRANSPORT_TS_NONMONOTONIC")


# -- R2: shape plausibility ---------------------------------------------------

def test_r2_subtype_priority():
    cand = decide(_ev(shape_ok=False,
                      health_flags={"B": ("NOISE", "STUCK")}),
                  SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.SENSOR_FAULT, "STUCK")
    assert cand.confidence == Conf.HIGH
    assert cand.reasons == ("SHAPE_NOISE", "SHAPE_STUCK")


def test_r2_range_beats_spike():
    cand = decide(_ev(shape_ok=False,
                      health_flags={"B": ("RANGE", "SPIKE")}),
                  SensorState.TRUSTED)
    assert cand.subtype == "RANGE"


# -- R2b: verbatim replay -----------------------------------------------------

def test_r2b_replay_with_unexplained_context():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=6.0,
                      up_trend=Trend.FLAT, replay_match=True),
                  SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.POSSIBLE_CYBER_ATTACK, "REPLAY")
    assert cand.confidence == Conf.HIGH
    assert cand.reasons == ("CONTEXT_PHANTOM", "REPLAY_MATCH")


def test_replay_booster_never_creates_on_consistent():
    cand = decide(_ev(replay_match=True), SensorState.TRUSTED)
    assert cand.label == Label.NORMAL


# -- R3: insufficient inputs --------------------------------------------------

def test_r3_notable_is_uncertain():
    cand = decide(_ev(context=Context.INSUFFICIENT, notable=True),
                  SensorState.TRUSTED)
    assert cand.label == Label.UNCERTAIN
    assert cand.rule_id == "R3"
    assert cand.reasons == ("INSUFFICIENT_INPUTS",)


def test_r3_normal_with_degraded_reason():
    cand = decide(_ev(context=Context.INSUFFICIENT, degraded=True),
                  SensorState.TRUSTED)
    assert cand.label == Label.NORMAL
    assert cand.reasons == ("INSUFFICIENT_INPUTS", "DEGRADED_INPUTS")


def test_r3_normal_plain():
    cand = decide(_ev(context=Context.INSUFFICIENT), SensorState.TRUSTED)
    assert cand.label == Label.NORMAL
    assert cand.reasons == ("INSUFFICIENT_INPUTS",)


# -- R4: consistent with physics ----------------------------------------------

def test_r4_quiet_consistent_is_normal():
    cand = decide(_ev(), SensorState.TRUSTED)
    assert cand.label == Label.NORMAL
    assert cand.reasons == ("CONTEXT_CONSISTENT",)


def test_r4_notable_without_rain_support_is_uncertain():
    cand = decide(_ev(notable=True, rain=Support.NO,
                      downstream=Downstream.UNKNOWN), SensorState.TRUSTED)
    assert cand.label == Label.UNCERTAIN
    assert cand.reasons == ("CONTEXT_CONSISTENT", "NO_RAIN_SUPPORT")


def test_r4_real_confidence_ladder():
    high = decide(_ev(notable=True, rain=Support.NO,
                      downstream=Downstream.YES), SensorState.TRUSTED)
    assert high.label == Label.REAL_FLOOD
    assert high.confidence == Conf.HIGH
    assert high.reasons == ("CONTEXT_CONSISTENT", "RAIN_NO",
                            "DOWNSTREAM_YES")

    med = decide(_ev(notable=True, rain=Support.YES), SensorState.TRUSTED)
    assert med.label == Label.REAL_FLOOD
    assert med.confidence == Conf.MED

    low = decide(_ev(notable=True, rain=Support.UNKNOWN,
                     downstream=Downstream.PENDING), SensorState.TRUSTED)
    assert low.label == Label.REAL_FLOOD
    assert low.confidence == Conf.LOW


# -- R5: phantom --------------------------------------------------------------

def test_r5_drift_is_fault():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=5.5, drift=True),
                  SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.SENSOR_FAULT, "DRIFT")
    assert cand.confidence == Conf.MED
    assert cand.reasons == ("CONTEXT_PHANTOM", "DRIFT_PATTERN")


def test_r5_fabricated_on_flat_upstream():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=5.5,
                      up_trend=Trend.FLAT, rain=Support.NO),
                  SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.POSSIBLE_CYBER_ATTACK,
                                          "FABRICATED")
    assert cand.confidence == Conf.MED


def test_r5_fabricated_on_falling_upstream():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=5.5,
                      up_trend=Trend.FALLING, rain=Support.UNKNOWN),
                  SensorState.TRUSTED)
    assert cand.subtype == "FABRICATED"


def test_r5_conflicting_on_rising_upstream():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=5.5,
                      up_trend=Trend.RISING, rain=Support.NO),
                  SensorState.TRUSTED)
    assert cand.label == Label.UNCERTAIN
    assert cand.confidence == Conf.MED
    assert "CONFLICTING_EVIDENCE" in cand.reasons


def test_r5_rain_yes_blocks_fabricated():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=5.5,
                      up_trend=Trend.FLAT, rain=Support.YES),
                  SensorState.TRUSTED)
    assert cand.label == Label.UNCERTAIN


# -- R6: suppressed -----------------------------------------------------------

def test_r6_drift_is_fault():
    cand = decide(_ev(context=Context.SUPPRESSED, z_mean=-5.5, drift=True),
                  SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.SENSOR_FAULT, "DRIFT")


def test_r6_suppression_on_rising_upstream():
    cand = decide(_ev(context=Context.SUPPRESSED, z_mean=-5.5,
                      up_trend=Trend.RISING, rain=Support.NO),
                  SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.POSSIBLE_CYBER_ATTACK,
                                          "SUPPRESSION")


def test_r6_suppression_with_rain():
    cand = decide(_ev(context=Context.SUPPRESSED, z_mean=-5.5,
                      up_trend=Trend.FLAT, rain=Support.YES),
                  SensorState.TRUSTED)
    assert cand.subtype == "SUPPRESSION"


def test_r6_unsupported_deviation():
    cand = decide(_ev(context=Context.SUPPRESSED, z_mean=-5.5,
                      up_trend=Trend.FALLING, rain=Support.UNKNOWN),
                  SensorState.TRUSTED)
    assert cand.label == Label.UNCERTAIN
    assert "UNSUPPORTED_DEVIATION" in cand.reasons


# -- R7: ambiguous ------------------------------------------------------------

def test_r7_notable_is_uncertain():
    cand = decide(_ev(context=Context.AMBIGUOUS, notable=True),
                  SensorState.TRUSTED)
    assert cand.label == Label.UNCERTAIN
    assert cand.reasons == ("AMBIGUOUS_RESIDUAL",)


def test_r7_suspect_sensor_is_uncertain():
    cand = decide(_ev(context=Context.AMBIGUOUS),
                  SensorState.SUSPECT)
    assert cand.label == Label.UNCERTAIN


def test_r7_quiet_ambiguous_is_normal():
    cand = decide(_ev(context=Context.AMBIGUOUS), SensorState.TRUSTED)
    assert cand.label == Label.NORMAL


# -- PA1: downstream mismatch -------------------------------------------------

def test_pa1_overrides_real():
    cand = decide(_ev(notable=True, rain=Support.YES,
                      downstream=Downstream.NO), SensorState.TRUSTED)
    assert (cand.label, cand.subtype) == (Label.POSSIBLE_CYBER_ATTACK,
                                          "DOWNSTREAM_MISMATCH")
    assert cand.confidence == Conf.MED
    assert cand.rule_id == "PA1"
    assert "DOWNSTREAM_NO" in cand.reasons


def test_pa1_overrides_uncertain():
    cand = decide(_ev(notable=True, rain=Support.NO,
                      downstream=Downstream.NO), SensorState.TRUSTED)
    assert cand.rule_id == "PA1"
    assert cand.label == Label.POSSIBLE_CYBER_ATTACK


# -- PA2: boosters ------------------------------------------------------------

def test_pa2_noise_too_clean_boosts_fabricated():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=5.5,
                      up_trend=Trend.FLAT, rain=Support.NO,
                      noise_too_clean=True), SensorState.TRUSTED)
    assert cand.subtype == "FABRICATED"
    assert cand.confidence == Conf.HIGH
    assert "NOISE_TOO_CLEAN" in cand.reasons


def test_pa2_replay_boosts_r1():
    cand = decide(_ev(transport_hard={"B": ("TS_SKEW",)},
                      replay_match=True), SensorState.TRUSTED)
    assert cand.label == Label.POSSIBLE_CYBER_ATTACK
    assert cand.confidence == Conf.HIGH
    assert "REPLAY_MATCH" in cand.reasons


def test_pa2_caps_at_high():
    cand = decide(_ev(context=Context.PHANTOM, z_mean=6.0,
                      up_trend=Trend.FLAT, replay_match=True,
                      noise_too_clean=True), SensorState.TRUSTED)
    assert cand.subtype == "REPLAY"
    assert cand.confidence == Conf.HIGH
    assert "NOISE_TOO_CLEAN" in cand.reasons


def test_booster_never_creates_normal_attack():
    cand = decide(_ev(noise_too_clean=True, replay_match=True),
                  SensorState.TRUSTED)
    assert cand.label == Label.NORMAL


# -- N-10: rate-of-rise is never sufficient -----------------------------------

def test_rate_exceeded_alone_stays_real():
    cand = decide(_ev(notable=True, rain=Support.YES,
                      rate_exceeded=True), SensorState.TRUSTED)
    assert cand.label == Label.REAL_FLOOD
    assert cand.confidence == Conf.MED


# -- §6 VerdictTracker ----------------------------------------------------------

def test_attack_commits_on_third_tick(persistence):
    t = tracker(persistence)
    cand = Candidate(Label.POSSIBLE_CYBER_ATTACK, "FABRICATED", Conf.MED, "R5")
    v1 = t.update(T0, 0, cand)
    assert v1.label == Label.NORMAL and not v1.changed
    assert (v1.persist_count, v1.persist_needed) == (1, 3)
    v2 = t.update(T0, 1, cand)
    assert v2.label == Label.NORMAL
    assert (v2.persist_count, v2.persist_needed) == (2, 3)
    v3 = t.update(T0, 2, cand)
    assert v3.label == Label.POSSIBLE_CYBER_ATTACK
    assert v3.changed is True
    assert (v3.persist_count, v3.persist_needed) == (3, 3)


def test_streak_resets_on_subtype_change(persistence):
    t = tracker(persistence)
    fab = Candidate(Label.POSSIBLE_CYBER_ATTACK, "FABRICATED", Conf.MED, "R5")
    sup = Candidate(Label.POSSIBLE_CYBER_ATTACK, "SUPPRESSION", Conf.MED, "R6")
    t.update(T0, 0, fab)
    t.update(T0, 1, fab)
    v = t.update(T0, 2, sup)
    assert v.label == Label.NORMAL
    assert (v.persist_count, v.persist_needed) == (1, 3)
    v = t.update(T0, 3, fab)
    assert v.label == Label.NORMAL
    assert (v.persist_count, v.persist_needed) == (1, 3)


def test_immediate_config_subtypes_commit_at_one_tick(persistence):
    for subtype in ("SIG_INVALID", "SEQ_REPLAY", "STATION_MISMATCH",
                    "UNKNOWN_STATION", "RANGE"):
        t = tracker(persistence)
        v = t.update(T0, 0, Candidate(Label.POSSIBLE_CYBER_ATTACK, subtype,
                                      Conf.HIGH, "R1"))
        assert v.label == Label.POSSIBLE_CYBER_ATTACK, subtype
        assert v.changed is True


def test_fast_fault_subtypes_commit_at_one_tick(persistence):
    for subtype in ("STUCK", "DROPOUT", "SPIKE"):
        t = tracker(persistence)
        v = t.update(T0, 0, Candidate(Label.SENSOR_FAULT, subtype,
                                      Conf.HIGH, "R2"))
        assert v.label == Label.SENSOR_FAULT, subtype


def test_non_immediate_hard_flag_takes_three_ticks(persistence):
    t = tracker(persistence)
    cand = Candidate(Label.POSSIBLE_CYBER_ATTACK, "TS_SKEW", Conf.MED, "R1")
    v = t.update(T0, 0, cand)
    assert v.label == Label.NORMAL
    v = t.update(T0, 1, cand)
    assert v.label == Label.NORMAL
    v = t.update(T0, 2, cand)
    assert v.label == Label.POSSIBLE_CYBER_ATTACK


def test_real_immediate_and_sticky_downgrade(persistence):
    t = tracker(persistence)
    real = Candidate(Label.REAL_FLOOD, None, Conf.MED, "R4")
    v = t.update(T0, 0, real)
    assert v.label == Label.REAL_FLOOD and v.changed

    unc = Candidate(Label.UNCERTAIN, None, Conf.LOW, "R7")
    v = t.update(T0, 1, unc)
    assert v.label == Label.REAL_FLOOD
    assert (v.persist_count, v.persist_needed) == (1, 3)
    v = t.update(T0, 2, unc)
    assert v.label == Label.REAL_FLOOD
    assert (v.persist_count, v.persist_needed) == (2, 3)
    v = t.update(T0, 3, unc)
    assert v.label == Label.UNCERTAIN and v.changed


def test_real_returns_instantly_after_uncertain(persistence):
    t = tracker(persistence)
    t.update(T0, 0, Candidate(Label.UNCERTAIN, None, Conf.LOW, "R7"))
    v = t.update(T0, 1, Candidate(Label.REAL_FLOOD, None, Conf.HIGH, "R4"))
    assert v.label == Label.REAL_FLOOD and v.changed is True


def test_normal_leaves_fault_slowly(persistence):
    t = tracker(persistence)
    noise = Candidate(Label.SENSOR_FAULT, "NOISE", Conf.MED, "R2")
    for i in range(3):
        v = t.update(T0, i, noise)
    assert v.label == Label.SENSOR_FAULT

    normal = Candidate(Label.NORMAL, None, Conf.LOW, "R4")
    v = t.update(T0, 3, normal)
    assert v.label == Label.SENSOR_FAULT
    assert (v.persist_count, v.persist_needed) == (1, 3)
    v = t.update(T0, 4, normal)
    assert v.label == Label.SENSOR_FAULT
    v = t.update(T0, 5, normal)
    assert v.label == Label.NORMAL and v.changed


def test_normal_staying_normal_is_one_tick(persistence):
    t = tracker(persistence)
    normal = Candidate(Label.NORMAL, None, Conf.LOW, "R4")
    t.update(T0, 0, normal)
    v = t.update(T0, 1, normal)
    assert v.label == Label.NORMAL
    assert (v.persist_count, v.persist_needed) == (1, 1)
    assert v.changed is False


def test_uncertain_commits_immediately_on_normal(persistence):
    t = tracker(persistence)
    v = t.update(T0, 0, Candidate(Label.UNCERTAIN, None, Conf.LOW, "R3"))
    assert v.label == Label.UNCERTAIN and v.changed


def test_verdict_fields_passthrough(persistence):
    t = tracker(persistence)
    cand = Candidate(Label.POSSIBLE_CYBER_ATTACK, "FABRICATED", Conf.MED,
                     "R5", ("CONTEXT_PHANTOM", "UPSTREAM_FLAT"))
    v = t.update(T0, 7, cand)
    assert v.ts == T0 and v.tick_idx == 7
    assert v.candidate == Label.POSSIBLE_CYBER_ATTACK
    assert v.candidate_subtype == "FABRICATED"
    assert v.label == Label.NORMAL
    assert v.subtype is None
    assert v.confidence == Conf.MED
    assert v.rule_id == "R5"
    assert v.reasons == ("CONTEXT_PHANTOM", "UPSTREAM_FLAT")


# -- integration: decide + tracker ---------------------------------------------

def test_real_flood_end_to_end(persistence):
    cand = decide(_ev(notable=True, rain=Support.YES,
                      downstream=Downstream.YES), SensorState.TRUSTED)
    assert cand.label == Label.REAL_FLOOD
    assert cand.confidence == Conf.HIGH
    t = tracker(persistence)
    v = t.update(T0, 0, cand)
    assert v.label == Label.REAL_FLOOD
    assert v.persist_needed == 1
