"""T033 response-integration tests: model-exception containment (ARCHITECTURE
§14), the verdict-to-response flow (quarantine, policy actions, messages,
community escalation) and the operator paths (approve/reject, ACK_RELEASE).

Synthetic signed ticks keep the model in warmup (required_history_ticks=192),
so the decision engine runs the R3 path and the response wiring is exercised
deterministically without a dataset.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from stageproof.data import load_model_artifact
from stageproof.domain import (Action, ActionStatus, ActionType, AlertState,
                               Label, SensorReading, SensorState, TickInput,
                               VolunteerReply)
from stageproof.models import ModelBundle
from stageproof.pipeline import Runner
from stageproof.security.audit import AuditLog, load as load_audit
from stageproof.security.audit import verify as verify_audit
from stageproof.security.transport import sign_reading
from stageproof.settings import load_settings

ROLES = ("A", "T", "B", "C")
QUIET = {"A": 9.0, "T": 3.5, "B": 5.0, "C": 7.0}
START = datetime(2023, 8, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def env():
    settings = load_settings("config", env_path="nonexistent.env")
    settings.env.update({f"STAGEPROOF_KEY_{r}": f"demo-key-{r}"
                         for r in ROLES})
    artifact = load_model_artifact("artifacts/model.json")
    settings.attach_model(artifact)
    return settings, artifact


def _ticks(settings, count, b_stage=5.0):
    """Signed synthetic ticks with constant quiet stages. Zero diffs keep B's
    NOISE burst detector quiet (STUCK needs pred_change, absent in warmup;
    TOO_CLEAN is informational only), so recovery tests see clean streaks."""
    seq = {r: 0 for r in ROLES}
    ticks = []
    for i in range(count):
        ts = START + timedelta(minutes=15 * i)
        readings = {}
        for r in ROLES:
            seq[r] += 1
            stage = b_stage if r == "B" else QUIET[r]
            unit = settings.reach.units["stage"]
            readings[r] = SensorReading(
                station_id=r, ts=ts, seq=seq[r], stage=stage, unit=unit,
                sig=sign_reading(r, ts, seq[r], stage, unit,
                                 settings.station_key(r)))
        ticks.append(TickInput(ts=ts, tick_idx=i, readings=readings,
                               rain_prev_hr_mm=0.0, rain_feed_ok=True))
    return ticks


def _bad_sig(tick):
    bad = replace(tick.readings["B"], sig="00" * 32)
    return TickInput(ts=tick.ts, tick_idx=tick.tick_idx,
                     readings={**tick.readings, "B": bad},
                     rain_prev_hr_mm=tick.rain_prev_hr_mm,
                     rain_feed_ok=tick.rain_feed_ok)


def _runner(env, tmp_path, name):
    settings, artifact = env
    return Runner(settings, ModelBundle.from_dict(artifact),
                  AuditLog(tmp_path / f"{name}.jsonl"))


def test_model_exception_contained_uncertain(env, tmp_path, monkeypatch):
    """T033 acceptance: an injected model exception does not crash the run;
    it yields SYSTEM_ERROR plus UNCERTAIN / INSUFFICIENT_INPUTS when the
    tick is notable, with the full response flow behind it."""
    settings, _ = env
    runner = _runner(env, tmp_path, "boom")
    runner.start("boom", 0, START)

    def boom(window, trust=None):
        raise RuntimeError("injected model failure")

    monkeypatch.setattr(runner.bundle, "predict_target", boom)

    ticks = _ticks(settings, 2, b_stage=14.6)
    rec = runner.step(ticks[0])

    kinds = [e.kind for e in rec.audit_events_new]
    assert "SYSTEM_ERROR" in kinds
    assert rec.prediction.error is not None
    assert rec.verdict.label == Label.UNCERTAIN
    assert "INSUFFICIENT_INPUTS" in rec.verdict.reasons
    assert rec.alert_state == AlertState.WATCH
    assert any(m.channel == "OFFICER" and "cannot be verified" in m.text
               for m in rec.new_messages)
    assert rec.verification is not None
    assert rec.verification["status"] == "OPEN"
    assert len([m for m in rec.new_messages
                if m.audience == "VOLUNTEER"]) == 4

    events = load_audit(runner.audit.path)
    assert any(e.kind == "SYSTEM_ERROR"
               and e.payload["component"] == "models.predict_target"
               for e in events)
    assert verify_audit(runner.audit.path).ok

    rec2 = runner.step(ticks[1])
    assert rec2.verdict.label == Label.UNCERTAIN
    assert not rec2.verdict.changed
    runner.finish()


def test_downstream_model_exception_contained(env, tmp_path, monkeypatch):
    """A predict_downstream exception is contained the same way and the run
    continues on the next tick."""
    settings, _ = env
    runner = _runner(env, tmp_path, "boom-c")
    runner.start("boom-c", 0, START)

    def boom(window):
        raise RuntimeError("downstream model failure")

    monkeypatch.setattr(runner.bundle, "predict_downstream", boom)

    ticks = _ticks(settings, 2)
    rec = runner.step(ticks[0])
    assert rec.downstream_prediction.error is not None
    events = [e for e in rec.audit_events_new if e.kind == "SYSTEM_ERROR"]
    assert events[0].payload["component"] == "models.predict_downstream"
    assert rec.verdict.label == Label.NORMAL
    rec2 = runner.step(ticks[1])
    assert rec2.verdict.label == Label.NORMAL


def test_attack_commit_response_flow(env, tmp_path):
    """SIG_INVALID commit drives the whole response: quarantine, tier-0
    actions, officer notice, estimate in use; no public warning exists."""
    settings, _ = env
    runner = _runner(env, tmp_path, "attack")
    runner.start("attack", 0, START)
    ticks = _ticks(settings, 5)
    for t in ticks[:3]:
        rec = runner.step(t)
        assert rec.verdict.label == Label.NORMAL

    before = runner.audit.n_events
    rec = runner.step(_bad_sig(ticks[3]))
    kinds = [e.kind for e in rec.audit_events_new]

    assert rec.accepted["B"] is False
    assert rec.verdict.label == Label.POSSIBLE_CYBER_ATTACK
    assert rec.verdict.subtype == "SIG_INVALID"
    assert kinds[0] == "TRANSPORT_FAILURE"
    assert kinds[1] == "VERDICT_CHANGE"
    assert runner.audit.n_events == before + len(kinds)
    assert rec.sensor_status["B"].state == SensorState.QUARANTINED
    assert rec.sensor_status["B"].needs_ack is True
    assert "SENSOR_STATE" in kinds
    executed_types = {a.type for a in rec.new_actions}
    assert {ActionType.QUARANTINE_SENSOR, ActionType.RAISE_SECURITY_ALERT,
            ActionType.PRESERVE_EVIDENCE, ActionType.NOTIFY_OFFICER,
            ActionType.USE_ESTIMATE} <= executed_types
    assert not any(a.type == ActionType.SEND_PUBLIC_WARNING
                   for a in rec.new_actions)
    # Estimate is computed at step 10 from the PRE-update b_state (sensor
    # machines update at step 11), so it goes in-use one tick AFTER the
    # commit, not on the commit tick itself.
    assert rec.estimate.in_use is False
    assert "ESTIMATE_IN_USE" not in kinds
    assert rec.alert_state == AlertState.NONE
    assert any(m.channel == "OFFICER" and "POSSIBLE CYBER ATTACK" in m.text
               for m in rec.new_messages)
    assert not any(m.audience == "public" for m in rec.new_messages)

    rec4 = runner.step(ticks[4])
    assert rec4.sensor_status["B"].state == SensorState.QUARANTINED
    assert rec4.estimate.in_use is True
    assert "ESTIMATE_IN_USE" in [e.kind for e in rec4.audit_events_new]


def test_quarantine_recovery_needs_ack_release(env, tmp_path):
    """An ATTACK quarantine holds until the officer ACK_RELEASE (tier 2,
    human only); recovery then goes through RECOVERING with the estimate
    basis dropping out at TRUSTED."""
    settings, _ = env
    runner = _runner(env, tmp_path, "recover")
    runner.start("recover", 0, START)
    ticks = _ticks(settings, 30)
    for t in ticks[:3]:
        runner.step(t)
    rec = runner.step(_bad_sig(ticks[3]))
    assert rec.sensor_status["B"].state == SensorState.QUARANTINED

    for t in ticks[4:10]:
        rec = runner.step(t)
        assert rec.sensor_status["B"].state == SensorState.QUARANTINED

    act = runner.ack_release("OFF-1")
    assert act.type == ActionType.ACK_RELEASE
    assert act.status == ActionStatus.EXECUTED
    assert act.approver == "OFF-1"
    assert runner._sensor["B"].needs_ack is False
    approvals = [e for e in load_audit(runner.audit.path)
                 if e.kind == "APPROVAL"]
    assert approvals[-1].payload["officer"] == "OFF-1"

    rec10 = runner.step(ticks[10])
    assert any(op["kind"] == "ACK_RELEASE"
               for op in rec10.operator_events)

    states = [rec10.sensor_status["B"].state]
    for t in ticks[11:]:
        rec = runner.step(t)
        if rec.sensor_status["B"].state != states[-1]:
            states.append(rec.sensor_status["B"].state)
        if states[-1] == SensorState.TRUSTED:
            break
    assert SensorState.RECOVERING in states
    assert states[-1] == SensorState.TRUSTED
    rec19 = runner.step(ticks[19])   # first tick whose PRE-update state is TRUSTED
    assert rec19.estimate.in_use is False
    assert rec19.best_source == "OBSERVED"


def test_verification_round_confirms_and_escalates(env, tmp_path,
                                                   monkeypatch):
    """A committed notable UNCERTAIN opens a volunteer round; two YES replies
    confirm it and the next tick escalates WATCH to a PROVISIONAL warning on
    the COMMUNITY basis, exactly once per episode."""
    settings, _ = env
    runner = _runner(env, tmp_path, "verify")
    runner.start("verify", 0, START)

    def boom(window, trust=None):
        raise RuntimeError("injected model failure")

    monkeypatch.setattr(runner.bundle, "predict_target", boom)

    ticks = _ticks(settings, 3, b_stage=14.6)
    rec = runner.step(ticks[0])
    assert rec.verdict.label == Label.UNCERTAIN
    assert rec.verification["status"] == "OPEN"
    rid = rec.verification["round_id"]

    ts1 = ticks[1].ts
    ok, detail = runner.submit_verification_reply(
        VolunteerReply(ts=ts1, volunteer_id="V1", code=1, round_id=rid))
    assert ok and detail == "recorded"
    ok2, detail2 = runner.submit_verification_reply(
        VolunteerReply(ts=ts1, volunteer_id="V3", code=1, round_id=rid))
    assert ok2 and detail2 == "confirmed"

    rec1 = runner.step(ticks[1])
    assert rec1.alert_state == AlertState.PROVISIONAL_WARNING
    assert rec1.alert_source == "COMMUNITY"
    warnings = [a for a in rec1.new_actions
                if a.type == ActionType.SEND_PUBLIC_WARNING]
    assert warnings and warnings[0].payload["level"] == "PROVISIONAL"
    assert any("FLOOD WARNING" in m.text
               and "reports from local volunteers" in m.text
               for m in rec1.new_messages if m.audience == "public")
    assert rec1.verification["status"] == "CONFIRMED"

    rec2 = runner.step(ticks[2])
    assert rec2.alert_state == AlertState.PROVISIONAL_WARNING
    assert not any(a.type == ActionType.SEND_PUBLIC_WARNING
                   for a in rec2.new_actions)


def test_operator_approve_and_reject(env, tmp_path):
    """Officer approve executes after the APPROVAL audit event (N-5);
    reject records the decision and never executes."""
    settings, _ = env
    runner = _runner(env, tmp_path, "ops")
    runner.start("ops", 0, START)
    ticks = _ticks(settings, 3)
    runner.step(ticks[0])

    ts = ticks[1].ts
    approve_me = Action(action_id="ACT-OPS-1", ts=ts,
                        type=ActionType.SEND_PUBLIC_WARNING, tier=1,
                        status=ActionStatus.PENDING, target="B",
                        payload={"level": "PROVISIONAL",
                                 "level_text": "17.5 ft",
                                 "basis_key": "observed"})
    reject_me = Action(action_id="ACT-OPS-2", ts=ts,
                       type=ActionType.RECOMMEND_EVACUATION, tier=2,
                       status=ActionStatus.PENDING, target="B", payload={})
    runner._approvals.register([approve_me, reject_me])
    runner._approved.append(approve_me)
    runner._approved.append(reject_me)

    assert {a.action_id for a in runner.pending_actions()} == \
        {"ACT-OPS-1", "ACT-OPS-2"}

    act = runner.approve("ACT-OPS-1", "OFF-7")
    assert act.status == ActionStatus.EXECUTED
    assert act.approver == "OFF-7"
    assert act.executed_ts == ticks[0].ts

    rej = runner.reject("ACT-OPS-2", "OFF-7")
    assert rej.status == ActionStatus.REJECTED
    assert rej.executed_ts is None

    approvals = [e for e in load_audit(runner.audit.path)
                 if e.kind == "APPROVAL"]
    assert [e.payload["decision"] for e in approvals] == ["APPROVED",
                                                          "REJECTED"]

    rec = runner.step(ticks[1])
    assert [(op["kind"], op["decision"]) for op in rec.operator_events] == \
        [("APPROVAL", "APPROVED"), ("APPROVAL", "REJECTED")]
