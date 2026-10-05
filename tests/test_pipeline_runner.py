"""T031 Runner-core and T032 transport/audit integration tests.

The T031 acceptance input is a clean replay: signed ticks from a quiet
stretch of the held-out (test-split) tail of data/reach.csv. T032 covers
SESSION events, TRANSPORT_FAILURE, and the fail-closed HALTED_AUDIT path.
"""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from stageproof.data import load_dataset, load_model_artifact
from stageproof.domain import (Action, ActionStatus, ActionType, AlertState,
                               Label, SensorReading, TickInput)
from stageproof.models import ModelBundle
from stageproof.pipeline import Runner, TickOrderError
from stageproof.security.audit import AuditError, AuditLog, load as load_audit
from stageproof.security.transport import sign_reading
from stageproof.settings import load_settings

REPLAY_TICKS = 700


@pytest.fixture(scope="module")
def replay_feed():
    """Settings + artifact + signed clean ticks from a quiet held-out
    stretch of data/reach.csv."""
    settings = load_settings("config", env_path="nonexistent.env")
    settings.env.update({f"STAGEPROOF_KEY_{r}": f"demo-key-{r}"
                         for r in "ATBC"})
    artifact = load_model_artifact("artifacts/model.json")
    settings.attach_model(artifact)

    df = load_dataset("data/reach.csv")
    fit = json.loads(Path("artifacts/fit_report.json").read_text())
    start_row = fit["rows"]["train"] + fit["rows"]["calibration"]
    sub = df.iloc[start_row:]

    roles = ("A", "T", "B", "C")
    chosen = None
    for s in range(0, len(sub) - REPLAY_TICKS, 100):
        w = sub.iloc[s:s + REPLAY_TICKS]
        if w[[f"{r}_stage" for r in roles]].isna().to_numpy().any():
            continue
        if not 2.0 < w["B_stage"].min() or not w["B_stage"].max() < 12.0:
            continue
        if w["A_stage"].max() >= 15.0 or w["T_stage"].max() >= 15.0:
            continue
        if w["C_stage"].min() <= 2.0 or w["C_stage"].max() >= 20.0:
            continue
        if w["rain_mm_prev_hr"].max() > 5.0:
            continue
        # Active enough that a live gauge never looks STUCK or TOO_CLEAN.
        if float(w["B_stage"].diff().std()) < 0.012:
            continue
        chosen = w
        break
    assert chosen is not None, "no quiet held-out stretch found"

    unit = settings.reach.units["stage"]
    ticks = []
    seq = {r: 0 for r in roles}
    for i, (ts, row) in enumerate(chosen.iterrows()):
        stamp = ts.to_pydatetime()
        readings = {}
        for r in roles:
            seq[r] += 1
            stage = float(row[f"{r}_stage"])
            readings[r] = SensorReading(
                station_id=r, ts=stamp, seq=seq[r], stage=stage, unit=unit,
                sig=sign_reading(r, stamp, seq[r], stage, unit,
                                 settings.station_key(r)))
        rain = row["rain_mm_prev_hr"]
        ticks.append(TickInput(ts=stamp, tick_idx=i, readings=readings,
                               rain_prev_hr_mm=None if pd.isna(rain)
                               else float(rain),
                               rain_feed_ok=True))
    return settings, artifact, ticks


def _make_runner(settings, artifact, tmp_path, name="audit"):
    return Runner(settings, ModelBundle.from_dict(artifact),
                  AuditLog(tmp_path / f"{name}.jsonl"))


def test_clean_replay_normal_no_transitions(tmp_path, replay_feed):
    """T031 acceptance: a clean replay of held-out data yields NORMAL
    verdicts with no transitions."""
    settings, artifact, ticks = replay_feed
    runner = _make_runner(settings, artifact, tmp_path)
    runner.start("clean-replay", 0, ticks[0].ts)
    records = [runner.step(t) for t in ticks]
    head_after_ticks = runner.audit.head_hash
    runner.finish()

    assert len(records) == REPLAY_TICKS
    assert {r.verdict.label for r in records} == {Label.NORMAL}
    assert not any(r.verdict.changed for r in records)
    assert {r.alert_state for r in records} == {AlertState.NONE}
    unaccepted = {role for rec in records
                  for role, ok in rec.accepted.items() if not ok}
    assert not unaccepted
    last = records[-1]
    assert set(last.baselines) == {"threshold_alert", "rollz_value",
                                   "rollz_flag"}
    assert last.baselines["rollz_value"] is not None
    assert last.estimate is not None and last.estimate.in_use is False
    assert last.audit_head_hash == head_after_ticks


def test_session_audit_events(tmp_path, replay_feed):
    """T032: SESSION_START carries scenario id/seed/model/config hashes;
    SESSION_END closes the run."""
    settings, artifact, ticks = replay_feed
    runner = _make_runner(settings, artifact, tmp_path, "session")
    runner.start("sess", 7, ticks[0].ts)
    for tick in ticks[:5]:
        runner.step(tick)
    runner.finish()

    events = load_audit(runner.audit.path)
    assert events[0].kind == "SESSION_START"
    assert events[0].payload["scenario_id"] == "sess"
    assert events[0].payload["seed"] == 7
    assert len(events[0].payload["model_hash"]) == 64
    assert len(events[0].payload["config_hash"]) == 64
    assert events[-1].kind == "SESSION_END"
    assert events[-1].payload["ticks"] == 5


def test_transport_failure_rejected_and_audited(tmp_path, replay_feed):
    """T032: an invalid signature is a HARD flag — packet treated as
    missing and a TRANSPORT_FAILURE event is appended."""
    settings, artifact, ticks = replay_feed
    runner = _make_runner(settings, artifact, tmp_path, "transport")
    runner.start("transport", 0, ticks[0].ts)
    for tick in ticks[:3]:
        runner.step(tick)

    bad = replace(ticks[3].readings["B"], sig="00" * 32)
    tick_bad = TickInput(ts=ticks[3].ts, tick_idx=3,
                         readings={**ticks[3].readings, "B": bad},
                         rain_prev_hr_mm=ticks[3].rain_prev_hr_mm)
    before = runner.audit.n_events
    record = runner.step(tick_bad)

    assert record.accepted["B"] is False
    assert record.transport_flags["B"] == ("SIG_INVALID",)
    assert record.evidence.transport_hard["B"] == ("SIG_INVALID",)
    kinds = [e.kind for e in record.audit_events_new]
    assert kinds[0] == "TRANSPORT_FAILURE"
    assert record.audit_events_new[0].payload["station"] == "B"
    assert runner.audit.n_events == before + len(kinds)
    # SIG_INVALID is an immediate subtype: R1 commits the ATTACK verdict.
    assert record.verdict.label == Label.POSSIBLE_CYBER_ATTACK
    assert record.verdict.subtype == "SIG_INVALID"
    assert kinds[1] == "VERDICT_CHANGE"


def test_forced_audit_failure_halts_execution(tmp_path, replay_feed):
    """T032 acceptance: forced audit write failure halts action execution
    and flags halted_audit (sticky, fail closed)."""
    settings, artifact, ticks = replay_feed
    runner = _make_runner(settings, artifact, tmp_path, "halt")
    runner.start("halt", 0, ticks[0].ts)
    runner.step(ticks[1])
    runner.step(ticks[2])
    action = Action(action_id="act-halt-1", ts=ticks[2].ts,
                    type=ActionType.QUARANTINE_SENSOR, tier=1,
                    status=ActionStatus.APPROVED, target="B")
    runner._approved.append(action)

    orig = runner.audit.append

    def boom(ts, kind, payload):
        raise AuditError("forced write failure")

    runner.audit.append = boom
    try:
        record = runner.step(ticks[3])
    finally:
        runner.audit.append = orig

    assert record.halted_audit is True
    assert record.audit_events_new == ()
    assert action.status == ActionStatus.APPROVED
    assert action.executed_ts is None

    frozen = runner.audit.n_events
    record2 = runner.step(ticks[4])
    assert record2.halted_audit is True          # sticky halt
    assert record2.audit_events_new == ()
    assert runner.audit.n_events == frozen
    assert record2.verdict is not None           # engine keeps observing


def test_tick_order_error(tmp_path, replay_feed):
    """T031: non-increasing tick ts raises TickOrderError; state is
    unchanged by the rejected ticks."""
    settings, artifact, ticks = replay_feed
    runner = _make_runner(settings, artifact, tmp_path, "order")
    runner.start("order", 0, ticks[0].ts)
    runner.step(ticks[1])
    with pytest.raises(TickOrderError):
        runner.step(TickInput(ts=ticks[1].ts, tick_idx=2, readings={}))
    with pytest.raises(TickOrderError):
        runner.step(TickInput(ts=ticks[0].ts, tick_idx=2, readings={}))
    record = runner.step(ticks[2])
    assert record.tick_idx == 2
