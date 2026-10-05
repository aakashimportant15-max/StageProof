"""T026-T030 acceptance: sensor/alert state machines, policy actions,
message templates, community verification rounds."""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone

import pytest

from stageproof.community import (MessageError, VerificationManager,
                                  basis_phrase, make_message, render)
from stageproof.domain import (ActionStatus, AlertState, Conf, Context,
                               Downstream, Estimate, Label, SensorState,
                               Verdict, VolunteerReply)
from stageproof.response import (ActionPolicy, AlertStateMachine, Approvals,
                                 SensorStateMachine, best_level)
from stageproof.settings import Policy, load_settings

TS0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def settings():
    return load_settings("config", ".env")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def commit(label: Label, subtype: str | None = None,
           tick_idx: int = 0) -> Verdict:
    return Verdict(ts=TS0, tick_idx=tick_idx, candidate=label, label=label,
                   subtype=subtype, confidence=Conf.HIGH, changed=True)


def est(lo: float, hi: float, conf: Conf = Conf.MED) -> Estimate:
    return Estimate(ts=TS0, stage_hat=(lo + hi) / 2, stage_lo=lo, stage_hi=hi,
                    q_hat=None, variant="A+T", basis=("A", "T"),
                    confidence=conf, in_use=True)


LEVELS = {"watch_stage": 14.0, "action_stage": 17.0, "clear_stage": 15.0}


# ---------------------------------------------------------------------------
# T026 — sensor state machines (RULES §7 transition table)
# ---------------------------------------------------------------------------

def test_soft_flag_streak_suspects_any_role(settings):
    th = settings.thresholds
    for role in ("A", "B", "C"):
        m = SensorStateMachine(role, th)
        assert m.update(0, soft=True).state == SensorState.TRUSTED
        assert m.update(1, soft=True).state == SensorState.SUSPECT
        assert m.reason == "consecutive soft transport flags"


def test_B_fault_candidate_suspects_on_first_tick(settings):
    m = SensorStateMachine("B", settings.thresholds)
    st = m.update(7, candidate=Label.SENSOR_FAULT)
    assert st.state == SensorState.SUSPECT
    assert st.since_tick == 7


def test_B_notable_uncertain_inconsistent_suspects(settings):
    m = SensorStateMachine("B", settings.thresholds)
    assert m.update(0, candidate=Label.UNCERTAIN, notable=True,
                    context=Context.AMBIGUOUS).state == SensorState.SUSPECT
    m2 = SensorStateMachine("B", settings.thresholds)
    assert m2.update(0, candidate=Label.UNCERTAIN, notable=True,
                     context=Context.CONSISTENT).state == SensorState.TRUSTED


def test_commit_from_suspect_quarantines(settings):
    m = SensorStateMachine("B", settings.thresholds)
    m.update(0, candidate=Label.SENSOR_FAULT)
    st = m.update(1, candidate=Label.SENSOR_FAULT,
                  verdict=commit(Label.SENSOR_FAULT, tick_idx=1))
    assert st.state == SensorState.QUARANTINED
    assert st.needs_ack is False          # FAULT release needs no officer ack
    assert m.last_transitions[0].entity == "sensor:B"


def test_immediate_subtype_quarantines_from_trusted(settings):
    m = SensorStateMachine("B", settings.thresholds)
    st = m.update(0, verdict=commit(Label.POSSIBLE_CYBER_ATTACK,
                                    subtype="SIG_INVALID"))
    assert st.state == SensorState.QUARANTINED
    assert st.needs_ack is True           # ATTACK release is gated on ack


def test_non_immediate_commit_from_trusted_is_fail_safe(settings):
    m = SensorStateMachine("B", settings.thresholds)
    st = m.update(0, verdict=commit(Label.SENSOR_FAULT))
    assert st.state == SensorState.QUARANTINED


def test_attack_quarantine_holds_until_ack_then_recovers(settings):
    m = SensorStateMachine("B", settings.thresholds)
    m.update(0, verdict=commit(Label.POSSIBLE_CYBER_ATTACK,
                               subtype="SEQ_REPLAY"))
    for t in range(1, 5):                 # 4 clean ticks: still ack-gated
        assert m.update(t, candidate=Label.NORMAL).state == \
            SensorState.QUARANTINED
    assert m.ack_release() is True
    assert m.ack_release() is False
    st = m.update(5, candidate=Label.NORMAL)
    assert st.state == SensorState.RECOVERING
    for t in range(6, 13):                # 7 further clean: still recovering
        assert m.update(t, candidate=Label.NORMAL).state == \
            SensorState.RECOVERING
    assert m.update(13, candidate=Label.NORMAL).state == SensorState.TRUSTED


def test_fault_quarantine_recovers_without_ack(settings):
    m = SensorStateMachine("B", settings.thresholds)
    m.update(0, candidate=Label.SENSOR_FAULT)
    m.update(1, candidate=Label.SENSOR_FAULT,
             verdict=commit(Label.SENSOR_FAULT, tick_idx=1))
    assert m.needs_ack is False
    for t in range(2, 5):                 # 3 clean ticks: not enough yet
        assert m.update(t, candidate=Label.NORMAL).state == \
            SensorState.QUARANTINED
    assert m.update(5, candidate=Label.NORMAL).state == SensorState.RECOVERING


def test_recovering_relapses_on_fault_candidate(settings):
    m = SensorStateMachine("B", settings.thresholds)
    m.update(0, verdict=commit(Label.SENSOR_FAULT))
    for t in range(1, 5):
        m.update(t, candidate=Label.NORMAL)
    assert m.state == SensorState.RECOVERING
    assert m.update(5, candidate=Label.SENSOR_FAULT).state == \
        SensorState.QUARANTINED


def test_ATC_health_flag_path_suspect_then_quarantine(settings):
    m = SensorStateMachine("A", settings.thresholds)
    assert m.update(0, shape=True).state == SensorState.SUSPECT
    assert m.update(1, shape=True).state == SensorState.SUSPECT
    st = m.update(2, shape=True)          # fault_attack_ticks = 3
    assert st.state == SensorState.QUARANTINED


def test_ATC_recovering_relapses_on_shape_flag(settings):
    m = SensorStateMachine("T", settings.thresholds)
    m.update(0, shape=True)
    m.update(1, shape=True)
    m.update(2, shape=True)
    for t in range(3, 7):
        m.update(t)
    assert m.state == SensorState.RECOVERING
    assert m.update(7, shape=True).state == SensorState.QUARANTINED


def test_B_uncertain_alone_never_quarantines(settings):
    m = SensorStateMachine("B", settings.thresholds)
    assert m.update(0, candidate=Label.UNCERTAIN, notable=True,
                    context=Context.INSUFFICIENT).state == SensorState.SUSPECT
    for t in range(1, 30):
        st = m.update(t, candidate=Label.UNCERTAIN, notable=True,
                      context=Context.INSUFFICIENT)
        assert st.state == SensorState.SUSPECT


def test_suspect_downgrades_to_trusted_after_clean_streak(settings):
    m = SensorStateMachine("B", settings.thresholds)
    m.update(0, candidate=Label.SENSOR_FAULT)
    assert m.state == SensorState.SUSPECT
    for t in range(1, 3):
        assert m.update(t, candidate=Label.NORMAL).state == \
            SensorState.SUSPECT
    assert m.update(3, candidate=Label.NORMAL).state == SensorState.TRUSTED


def test_quarantine_entry_resets_clean_streak(settings):
    m = SensorStateMachine("B", settings.thresholds)
    m.update(0, verdict=commit(Label.SENSOR_FAULT))
    for t in range(1, 3):                 # 2 clean in quarantine
        m.update(t, candidate=Label.NORMAL)
    m.update(3, candidate=Label.SENSOR_FAULT)   # adverse: streak restarts
    assert m.state == SensorState.QUARANTINED
    for t in range(4, 7):                 # 3 clean: not enough yet
        assert m.update(t, candidate=Label.NORMAL).state == \
            SensorState.QUARANTINED
    assert m.update(7, candidate=Label.NORMAL).state == SensorState.RECOVERING


# ---------------------------------------------------------------------------
# T027 — alert machine (RULES §8) and best_level (§11)
# ---------------------------------------------------------------------------

def test_best_level_prefers_observed_only_when_trusted():
    e = est(12.0, 13.0)                    # stage_hat 12.5
    assert best_level(True, 12.5, e) == (12.5, "OBSERVED")
    assert best_level(False, 12.5, e) == (12.5, "ESTIMATE")
    assert best_level(False, None, None) == (None, None)
    assert best_level(False, float("nan"), e) == (12.5, "ESTIMATE")


def test_watch_entries_and_sources(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    ts = am.update(0, verdict_label=Label.UNCERTAIN, notable=True)
    assert am.state == AlertState.WATCH
    assert ts[0].to_state == "WATCH"
    am2 = AlertStateMachine(settings.thresholds, LEVELS)
    am2.update(0, verdict_label=Label.REAL_FLOOD, best_level=13.0,
               best_source="OBSERVED", obs_stage=13.0)
    assert am2.state == AlertState.NONE            # below watch stage
    am2.update(1, verdict_label=Label.REAL_FLOOD, best_level=15.0,
               best_source="OBSERVED", obs_stage=15.0)
    assert am2.state == AlertState.WATCH
    am3 = AlertStateMachine(settings.thresholds, LEVELS)
    am3.update(0, verdict_label=Label.NORMAL, in_use=True, estimate=est(15, 18))
    assert am3.state == AlertState.WATCH
    assert am3.source == "ESTIMATE"


def test_watch_idle_expires_to_none(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.UNCERTAIN, notable=True)
    for t in range(1, 8):
        am.update(t, verdict_label=Label.NORMAL)
        assert am.state == AlertState.WATCH
    am.update(8, verdict_label=Label.NORMAL)
    assert am.state == AlertState.NONE


def test_provisional_chains_from_none_in_one_tick(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    ts = am.update(0, verdict_label=Label.REAL_FLOOD, best_level=18.0,
                   best_source="OBSERVED", obs_stage=18.0)
    assert [(t.from_state, t.to_state, t.source) for t in ts] == [
        ("NONE", "WATCH", "OBSERVED"),
        ("WATCH", "PROVISIONAL_WARNING", "OBSERVED")]


def test_provisional_from_estimate_streak(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.NORMAL, in_use=True, estimate=est(15, 18))
    assert am.state == AlertState.WATCH
    # stage_lo crosses action stage; est_warn_ticks = 2 consecutive est_ok
    # ticks are required before the estimate may raise a public warning
    am.update(1, verdict_label=Label.NORMAL, in_use=True,
              estimate=est(17.5, 19.0))
    assert am.state == AlertState.WATCH
    ts = am.update(2, verdict_label=Label.NORMAL, in_use=True,
                   estimate=est(17.5, 19.0))
    assert am.state == AlertState.PROVISIONAL_WARNING
    assert ts[0].source == "ESTIMATE"


def test_confirmed_via_downstream_and_officer(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.REAL_FLOOD, best_level=18.0,
              best_source="OBSERVED", obs_stage=18.0)
    ts = am.update(1, verdict_label=Label.REAL_FLOOD, best_level=18.0,
                   best_source="OBSERVED", obs_stage=18.0,
                   downstream=Downstream.YES)
    assert am.state == AlertState.CONFIRMED_WARNING
    assert ts[0].source == "DOWNSTREAM"
    am2 = AlertStateMachine(settings.thresholds, LEVELS)
    am2.update(0, verdict_label=Label.REAL_FLOOD, best_level=18.0,
               best_source="OBSERVED", obs_stage=18.0)
    am2.officer_confirm()
    ts2 = am2.update(1, verdict_label=Label.REAL_FLOOD, best_level=18.0,
                     best_source="OBSERVED", obs_stage=18.0)
    assert am2.state == AlertState.CONFIRMED_WARNING
    assert ts2[0].source == "OFFICER"


def test_retraction_on_fault_commit_returns_to_watch(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.REAL_FLOOD, best_level=18.0,
              best_source="OBSERVED", obs_stage=18.0)
    ts = am.update(1, verdict_label=Label.SENSOR_FAULT, best_level=18.0,
                   best_source="OBSERVED", obs_stage=18.0)
    assert am.state == AlertState.WATCH
    assert ts[0].reason.startswith("retraction")


def test_community_confirmed_provisional_source_tag(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.UNCERTAIN, notable=True)
    ts = am.update(1, verdict_label=Label.UNCERTAIN, notable=True,
                   community_confirmed=True)
    assert am.state == AlertState.PROVISIONAL_WARNING
    assert ts[0].source == "COMMUNITY"


def test_clear_requires_clear_ticks_and_missing_data_resets(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.REAL_FLOOD, best_level=18.0,
              best_source="OBSERVED", obs_stage=18.0)
    for t in range(1, 4):                 # 3 low ticks
        am.update(t, verdict_label=Label.NORMAL, best_level=14.0,
                  best_source="OBSERVED", obs_stage=14.0)
    assert am.state == AlertState.PROVISIONAL_WARNING
    # lost data: never accumulates the clear streak (N-16)
    am.update(4, verdict_label=Label.NORMAL, best_level=None,
              best_source=None, obs_stage=None)
    for t in range(5, 12):                # 7 low ticks: still not enough
        am.update(t, verdict_label=Label.NORMAL, best_level=14.0,
                  best_source="OBSERVED", obs_stage=14.0)
        assert am.state == AlertState.PROVISIONAL_WARNING
    ts = am.update(12, verdict_label=Label.NORMAL, best_level=14.0,
                   best_source="OBSERVED", obs_stage=14.0)
    assert am.state == AlertState.CLEARED
    assert [(t.from_state, t.to_state) for t in ts] == [
        ("PROVISIONAL_WARNING", "CLEARED")]
    # CLEARED -> NONE happens on the next tick only (RULES §8)
    am.update(13, verdict_label=Label.NORMAL, best_level=14.0,
              best_source="OBSERVED", obs_stage=14.0)
    assert am.state == AlertState.NONE


def test_clear_on_estimate_uses_stage_hi(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.NORMAL, in_use=True, estimate=est(15, 18))
    am.update(1, verdict_label=Label.NORMAL, in_use=True,
              estimate=est(17.5, 19.0))
    am.update(2, verdict_label=Label.NORMAL, in_use=True,
              estimate=est(17.5, 19.0))
    assert am.state == AlertState.PROVISIONAL_WARNING
    for t in range(3, 10):
        am.update(t, verdict_label=Label.NORMAL, best_level=13.0,
                  best_source="ESTIMATE", in_use=True, estimate=est(12, 14))
        assert am.state == AlertState.PROVISIONAL_WARNING
    am.update(10, verdict_label=Label.NORMAL, best_level=13.0,
              best_source="ESTIMATE", in_use=True, estimate=est(12, 14))
    assert am.state == AlertState.CLEARED
    am.update(11, verdict_label=Label.NORMAL, best_level=13.0,
              best_source="ESTIMATE", in_use=True, estimate=est(12, 14))
    assert am.state == AlertState.NONE


def test_officer_clear_consumed_once(settings):
    am = AlertStateMachine(settings.thresholds, LEVELS)
    am.update(0, verdict_label=Label.REAL_FLOOD, best_level=18.0,
              best_source="OBSERVED", obs_stage=18.0)
    am.officer_clear()
    ts = am.update(1, verdict_label=Label.REAL_FLOOD, best_level=18.0,
                   best_source="OBSERVED", obs_stage=18.0)
    assert am.state == AlertState.CLEARED
    assert [t.to_state for t in ts] == ["CLEARED"]
    # next tick: CLEARED -> NONE, but the still-live REAL flood re-raises
    ts2 = am.update(2, verdict_label=Label.REAL_FLOOD, best_level=18.0,
                    best_source="OBSERVED", obs_stage=18.0)
    assert am.state == AlertState.PROVISIONAL_WARNING
    assert [t.to_state for t in ts2] == ["NONE", "WATCH",
                                         "PROVISIONAL_WARNING"]


# ---------------------------------------------------------------------------
# T028 — ActionPolicy and Approvals (RULES §12)
# ---------------------------------------------------------------------------

def test_tier_flip_changes_auto_approval():
    tiers = {"SEND_PUBLIC_WARNING": 1, "RECOMMEND_EVACUATION": 2,
             "SEND_ALL_CLEAR": 0}
    triggers = {"t": ["SEND_PUBLIC_WARNING", "RECOMMEND_EVACUATION",
                      "SEND_ALL_CLEAR"]}
    demo = ActionPolicy(Policy(demo_auto_approve_tier1=True, tiers=tiers,
                               triggers=triggers))
    acts = {a.type.name: a for a in demo.create("t", TS0)}
    assert acts["SEND_ALL_CLEAR"].status == ActionStatus.AUTO_APPROVED
    assert acts["SEND_PUBLIC_WARNING"].status == ActionStatus.AUTO_APPROVED
    assert acts["RECOMMEND_EVACUATION"].status == ActionStatus.PENDING

    strict = ActionPolicy(Policy(demo_auto_approve_tier1=False, tiers=tiers,
                                 triggers=triggers))
    acts2 = {a.type.name: a for a in strict.create("t", TS0)}
    assert acts2["SEND_PUBLIC_WARNING"].status == ActionStatus.PENDING
    assert acts2["RECOMMEND_EVACUATION"].status == ActionStatus.PENDING
    assert acts2["SEND_ALL_CLEAR"].status == ActionStatus.AUTO_APPROVED


def test_if_configured_gates_evacuation(settings):
    p = settings.policy
    none = ActionPolicy(p, configured={}).create("alert_confirmed_warning",
                                                 TS0)
    assert [a.type.name for a in none] == ["SEND_PUBLIC_WARNING"]
    gated = ActionPolicy(p, configured={"evac_stage": True})
    acts = gated.create("alert_confirmed_warning", TS0)
    evac = [a for a in acts if a.type.name == "RECOMMEND_EVACUATION"]
    assert len(evac) == 1
    assert evac[0].status == ActionStatus.PENDING       # Tier 2, human only
    # demo_auto_approve_tier1 is true in config, so the Tier 1 public warning
    # is auto-approved in the demo; Tier 2 evacuation still needs a human.
    plain = [a for a in acts if a.type.name == "SEND_PUBLIC_WARNING"]
    assert plain[0].payload.get("level") == "CONFIRMED"
    assert plain[0].status == ActionStatus.AUTO_APPROVED


def test_action_ids_are_unique_and_tiered(settings):
    p = ActionPolicy(settings.policy, configured={"evac_stage": True})
    acts = p.create("verdict_possible_cyber_attack", TS0, target="B")
    names = [a.type.name for a in acts]
    assert names == ["QUARANTINE_SENSOR", "RAISE_SECURITY_ALERT",
                     "PRESERVE_EVIDENCE", "NOTIFY_OFFICER", "USE_ESTIMATE"]
    assert len({a.action_id for a in acts}) == len(acts)
    assert all(a.target == "B" for a in acts)
    assert all(a.status == ActionStatus.AUTO_APPROVED for a in acts)


def test_approvals_record_officer_identity():
    ap = ActionPolicy(Policy(demo_auto_approve_tier1=False,
                             tiers={"SEND_PUBLIC_WARNING": 1,
                                    "RECOMMEND_EVACUATION": 2},
                             triggers={"t": ["SEND_PUBLIC_WARNING",
                                             "RECOMMEND_EVACUATION"]}))
    acts = ap.create("t", TS0)
    assert all(a.status == ActionStatus.PENDING for a in acts)
    reg = Approvals()
    reg.register(acts)
    pending = reg.pending()
    assert {a.type.name for a in pending} == {"SEND_PUBLIC_WARNING",
                                              "RECOMMEND_EVACUATION"}
    warn = next(a for a in acts if a.type.name == "SEND_PUBLIC_WARNING")
    evac = next(a for a in acts if a.type.name == "RECOMMEND_EVACUATION")
    out = reg.approve(evac.action_id, "officer-7")
    assert out.status == ActionStatus.APPROVED
    assert out.approver == "officer-7"
    rej = reg.reject(warn.action_id, "officer-7")
    assert rej.status == ActionStatus.REJECTED
    with pytest.raises(ValueError, match="not PENDING"):
        reg.approve(evac.action_id, "officer-8")
    with pytest.raises(KeyError):
        reg.get("ACT-99999")
    assert reg.pending() == ()


# ---------------------------------------------------------------------------
# T029 — template rendering (DESIGN §9.3)
# ---------------------------------------------------------------------------

def _sample_fields(text: str, extra: dict) -> dict:
    names = {fn for _, fn, _, _ in string.Formatter().parse(text) if fn}
    return {name: extra.get(name, "17.2 ft") for name in names}


def test_every_template_renders_en_and_hi(settings):
    msgs = settings.messages
    samples = {"station": "Cartersville", "level": "WATCH",
               "stage": "17.2 ft", "time": "2026-10-04 12:00",
               "variant": "A+T", "mark": "17.0 ft"}
    for key, langs in msgs.templates.items():
        fields_en = _sample_fields(langs["en"], samples)
        text_en = render(msgs, key, "en", **fields_en)
        assert isinstance(text_en, str) and text_en
        if "hi" in langs:
            fields_hi = _sample_fields(langs["hi"], samples)
            text_hi = render(msgs, key, "hi", **fields_hi)
            assert isinstance(text_hi, str) and text_hi


def test_basis_phrases_render(settings):
    msgs = settings.messages
    for basis in ("observed", "estimate", "community", "downstream"):
        assert basis_phrase(msgs, basis, "en")
        assert basis_phrase(msgs, basis, "hi")


def test_missing_field_and_unknown_template_raise(settings):
    msgs = settings.messages
    with pytest.raises(MessageError, match="unknown message template"):
        render(msgs, "no_such_template")
    with pytest.raises(MessageError, match="missing field"):
        render(msgs, "public_watch", "en")


def test_hi_falls_back_to_en_for_officer_templates(settings):
    msgs = settings.messages
    officer_keys = [k for k in msgs.templates if k.startswith("officer_")]
    assert officer_keys
    for key in officer_keys:
        fields = _sample_fields(msgs.templates[key]["en"], {})
        assert render(msgs, key, "hi", **fields) == \
            render(msgs, key, "en", **fields)


def test_make_message_builds_domain_object(settings):
    msgs = settings.messages
    msg = make_message("MSG-00001", TS0, msgs, "volunteer_prompt",
                       channel="SMS", audience="V1", lang="en",
                       fields={"station": "Cartersville"},
                       recipient_id="V1")
    assert msg.msg_id == "MSG-00001"
    assert "Cartersville" in msg.text
    assert msg.recipient_id == "V1"


# ---------------------------------------------------------------------------
# T030 — community verification rounds (RULES §13)
# ---------------------------------------------------------------------------

def reply(vid: str, code: int, round_id: str, ts=TS0) -> VolunteerReply:
    return VolunteerReply(ts=ts, volunteer_id=vid, code=code,
                          round_id=round_id)


def test_two_yes_replies_confirm(settings):
    vm = VerificationManager(settings, station="B",
                             station_name="Cartersville")
    rnd = vm.maybe_open(100, TS0, uncertain_notable=True)
    assert rnd is not None and rnd.status == "OPEN"
    assert len(rnd.prompts) == settings.thresholds.verification.max_volunteers
    ok1, why1 = vm.submit_reply(reply("V1", 1, rnd.round_id))
    assert (ok1, why1) == (True, "recorded")
    assert rnd.posterior() == pytest.approx(0.8)
    ok2, why2 = vm.submit_reply(reply("V2", 1, rnd.round_id))
    assert (ok2, why2) == (True, "confirmed")
    assert rnd.posterior() == pytest.approx(0.9412, abs=1e-3)
    assert vm.confirmed is True


def test_one_reply_is_not_enough(settings):
    vm = VerificationManager(settings)
    rnd = vm.maybe_open(0, TS0, uncertain_notable=True)
    ok, why = vm.submit_reply(reply("V1", 1, rnd.round_id))
    assert (ok, why) == (True, "recorded")
    assert vm.confirmed is False


def test_no_replies_refute(settings):
    vm = VerificationManager(settings)
    rnd = vm.maybe_open(0, TS0, uncertain_notable=True)
    vm.submit_reply(reply("V1", 2, rnd.round_id))
    ok, why = vm.submit_reply(reply("V2", 2, rnd.round_id))
    assert (ok, why) == (True, "refuted")
    assert rnd.status == "REFUTED"
    assert rnd.posterior() == pytest.approx(0.0588, abs=1e-3)


def test_code_three_never_moves_odds(settings):
    vm = VerificationManager(settings)
    rnd = vm.maybe_open(0, TS0, uncertain_notable=True)
    ok, why = vm.submit_reply(reply("V1", 3, rnd.round_id))
    assert (ok, why) == (True, "noted")
    assert rnd.informative == 0 and rnd.log_odds == 0.0
    vm.submit_reply(reply("V2", 1, rnd.round_id))
    assert vm.confirmed is False


def test_unregistered_duplicate_and_closed_rounds_ignored(settings):
    vm = VerificationManager(settings)
    rnd = vm.maybe_open(0, TS0, uncertain_notable=True)
    assert vm.submit_reply(reply("GHOST", 1, rnd.round_id)) == \
        (False, "unregistered")
    assert vm.submit_reply(reply("V1", 1, "VR-9999")) == \
        (False, "unknown round")
    assert vm.submit_reply(reply("V1", 1, rnd.round_id))[0] is True
    assert vm.submit_reply(reply("V1", 2, rnd.round_id)) == \
        (False, "duplicate")
    vm.submit_reply(reply("V2", 1, rnd.round_id))     # resolves CONFIRMED
    assert vm.submit_reply(reply("V3", 1, rnd.round_id)) == \
        (False, "no open round")


def test_timeout_escalates_and_cooldown_gates_reopen(settings):
    vm = VerificationManager(settings)
    rnd = vm.maybe_open(100, TS0, uncertain_notable=True)
    assert vm.tick(101, TS0) is None
    assert vm.tick(102, TS0) is None
    timed_out = vm.tick(104, TS0)         # 104 - 100 >= timeout_ticks (4)
    assert timed_out is rnd and rnd.status == "TIMEOUT"
    assert vm.submit_reply(reply("V1", 1, rnd.round_id)) == \
        (False, "no open round")
    # cooldown: 8 ticks from the last opening
    assert vm.maybe_open(105, TS0, uncertain_notable=True) is None
    assert vm.maybe_open(107, TS0, uncertain_notable=True) is None
    again = vm.maybe_open(108, TS0, uncertain_notable=True)
    assert again is not None and again.round_id != rnd.round_id
    # without the trigger flag nothing opens
    assert vm.maybe_open(200, TS0, uncertain_notable=False) is None


def test_no_trigger_flag_never_opens(settings):
    vm = VerificationManager(settings)
    assert vm.maybe_open(0, TS0, uncertain_notable=False) is None
    assert vm.summary() is None


def test_summary_shape(settings):
    vm = VerificationManager(settings)
    rnd = vm.maybe_open(10, TS0, uncertain_notable=True)
    vm.submit_reply(reply("V1", 1, rnd.round_id))
    s = vm.summary()
    assert s["round_id"] == rnd.round_id
    assert s["status"] == "OPEN"
    assert s["informative"] == 1
    assert s["posterior"] == pytest.approx(0.8)
    assert s["replies"] == {"V1": 1}
