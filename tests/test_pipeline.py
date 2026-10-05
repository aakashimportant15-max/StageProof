"""Pipeline-level tests: domain round-trip now; scenario golden A–F, no-leakage,
quarantine isolation, determinism and import layering are added by their TASKS
entries (T023/T024)."""

from datetime import datetime, timedelta, timezone

import pytest

from stageproof import domain
from stageproof.domain import (
    Action, ActionStatus, ActionType, AlertState, AuditEvent, Conf, Context,
    Downstream, Estimate, Evidence, Label, Message, Prediction, SensorReading,
    SensorState, SensorStatus, TickRecord, Support, Trend, Verdict,
    record_from_dict, record_to_dict,
)

import json


def _populated_record() -> TickRecord:
    ts = datetime(2023, 6, 15, 12, 0, tzinfo=timezone.utc)
    reading = SensorReading(
        station_id="B", ts=ts, seq=42, stage=5.25, unit="ft",
        sig="deadbeef",
    )
    prediction = Prediction(
        role="B", ts=ts, variant="A+T+R", inputs_used=("A", "T", "R"),
        logq_obs=2.5, logq_pred=1.25, scale=0.03, regime="normal", z=41.6,
        q_pred=950.5, stage_pred=4.8, degraded=False, error=None,
    )
    evidence = Evidence(
        ts=ts, tick_idx=100,
        transport_flags={"A": (), "T": (), "B": (), "C": ()},
        transport_hard={"A": (), "T": (), "B": (), "C": ()},
        health_flags={"B": ()},
        shape_ok=True, rate_exceeded=False,
        variant="A+T+R", z=41.6, z_mean=40.2, context=Context.PHANTOM,
        up_trend=Trend.FLAT, rain=Support.NO, downstream=Downstream.PENDING,
        z_down_mean=None, replay_match=False, noise_too_clean=False,
        drift=False, obs_stage=5.25, pred_stage=4.8, notable=True,
        degraded=False, trusted_inputs={"A": True, "T": True, "B": True, "C": True},
    )
    verdict = Verdict(
        ts=ts, tick_idx=100, candidate=Label.POSSIBLE_CYBER_ATTACK,
        candidate_subtype="FABRICATED", label=Label.POSSIBLE_CYBER_ATTACK,
        subtype="FABRICATED", confidence=Conf.HIGH, persist_count=3,
        persist_needed=3, rule_id="R5",
        reasons=("CONTEXT_PHANTOM", "UPSTREAM_FLAT", "RAIN_NO"), changed=True,
    )
    estimate = Estimate(
        ts=ts, stage_hat=3.1, stage_lo=2.9, stage_hi=3.3, q_hat=640.0,
        variant="A+T+R", basis=("A", "T", "R"), confidence=Conf.MED, in_use=True,
    )
    action = Action(
        action_id="act-000001", ts=ts, type=ActionType.QUARANTINE_SENSOR,
        tier=0, status=ActionStatus.EXECUTED, target="B",
        payload={"reason": "SENSOR_FAULT"}, approver=None,
        executed_ts=ts + timedelta(seconds=1), source="verdict_commit",
    )
    message = Message(
        msg_id="msg-000001", ts=ts, channel="OFFICER", audience="officer",
        language="en", text="notified", action_id="act-000002",
        recipient_id="OFF-1",
    )
    audit_event = AuditEvent(
        idx=7, ts=ts, kind="VERDICT_CHANGE",
        payload={"label": "POSSIBLE_CYBER_ATTACK"}, prev_hash="ab" * 32,
        hash="cd" * 32,
    )
    return TickRecord(
        tick_idx=100, ts=ts,
        readings={"A": None, "T": None, "B": reading, "C": None},
        accepted={"A": True, "T": True, "B": True, "C": True},
        transport_flags={"B": ()},
        prediction=prediction,
        downstream_prediction=None,
        evidence=evidence,
        verdict=verdict,
        estimate=estimate,
        sensor_status={
            "B": SensorStatus(role="B", state=SensorState.QUARANTINED,
                              since_tick=98, reason="ATTACK", clean_streak=0,
                              candidate_streak=3, needs_ack=False),
        },
        alert_state=AlertState.WATCH,
        alert_source="OBSERVED",
        best_level=5.25,
        best_source="OBSERVED",
        new_actions=(action,),
        new_messages=(message,),
        verification={"round_id": "r1", "open": True, "posterior": 0.5},
        baselines={"threshold_alert": True, "threshold_value": 5.25,
                   "rollz_value": 6.1, "rollz_flag": True},
        audit_events_new=(audit_event,),
        audit_head_hash="cd" * 32,
        operator_events=(),
        halted_audit=False,
        truth=None,
    )


def test_tickrecord_roundtrip():
    """T005 acceptance: a populated TickRecord survives dict + JSON round-trip."""
    record = _populated_record()
    as_dict = record_to_dict(record)
    json.dumps(as_dict)  # must be JSON-safe
    rebuilt = record_from_dict(json.loads(json.dumps(as_dict)))
    assert rebuilt == record


def test_domain_enum_roundtrip():
    ts = datetime(2023, 1, 1, tzinfo=timezone.utc)
    reading = SensorReading("A", ts, 1, 2.0, "ft", "ff")
    enc = record_to_dict(reading)
    assert enc["ts"] == ts.isoformat()
    assert domain.dataclass_from_dict(enc, SensorReading) == reading


def test_domain_no_package_imports():
    """T005 acceptance: domain imports nothing from other package modules."""
    import ast
    import pathlib
    src = pathlib.Path(domain.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("stageproof"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("stageproof"), alias.name


# ---------------------------------------------------------------------------
# T004 — settings loader
# ---------------------------------------------------------------------------

RULES5_SECTIONS = {
    "transport": ["ts_skew_max_seconds", "soft_flag_suspect_ticks"],
    "context": ["z_consistent", "z_implausible", "z_window_ticks",
                "scale_floor", "band_z"],
    "trend": ["up_window_ticks", "up_rising_min", "up_flat_max"],
    "rain": ["window_hours", "yes_mm", "no_fraction", "no_mm", "stale_hours"],
    "health": ["dropout_ticks", "stuck_ticks", "stuck_pred_change_min",
               "spike_revert_ticks", "spike_revert_tol_steps",
               "noise_window_ticks", "noise_ratio_max", "clean_window_ticks",
               "clean_ratio_min", "clean_min_move_steps"],
    "drift": ["max_dev", "max_rate_per_hour"],
    "replay": ["window_ticks", "tol_steps", "min_range_steps", "min_age_days"],
    "persistence": ["fault_attack_ticks", "real_downgrade_ticks",
                    "immediate_subtypes"],
    "sensor": ["clean_ticks_to_recover", "recovery_ticks"],
    "alert": ["watch_idle_ticks", "clear_ticks", "est_warn_ticks"],
    "verification": ["prior_real", "default_reliability", "confirm_posterior",
                     "refute_posterior", "min_replies", "timeout_ticks",
                     "cooldown_ticks", "max_volunteers"],
}


def test_load_settings_all_rules5_keys():
    """T004 acceptance: Settings carries every RULES §5 key with doc values."""
    from stageproof.settings import load_settings
    settings = load_settings("config", env_path="nonexistent.env")
    for section, keys in RULES5_SECTIONS.items():
        group = getattr(settings.thresholds, section)
        for key in keys:
            assert hasattr(group, key), f"{section}.{key} missing"
    assert settings.thresholds.transport.ts_skew_max_seconds == 300
    assert settings.thresholds.context.z_consistent == 3.0
    assert settings.thresholds.persistence.immediate_subtypes == (
        "SIG_INVALID", "SEQ_REPLAY", "STATION_MISMATCH", "UNKNOWN_STATION", "RANGE",
    )
    assert settings.thresholds.rain.yes_mm is None
    assert settings.thresholds.verification.max_volunteers == 4
    assert settings.policy.tier("RECOMMEND_EVACUATION") == 2
    assert settings.reach.tick_minutes == 15
    assert settings.messages.reasons  # Appendix A texts present


def test_missing_threshold_key_raises(tmp_path):
    """T004 acceptance: a missing key raises ConfigError naming it."""
    import shutil
    from stageproof.settings import ConfigError, load_settings
    tmp_cfg = tmp_path / "config"
    shutil.copytree("config", tmp_cfg)
    text = (tmp_cfg / "thresholds.yaml").read_text(encoding="utf-8")
    text = text.replace("  z_implausible: 5.0\n", "")
    (tmp_cfg / "thresholds.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="z_implausible"):
        load_settings(tmp_cfg, env_path=tmp_path / "no.env")


def test_threshold_precedence_yaml_over_model():
    """RULES §5 precedence: yaml non-null > model.json > ConfigError."""
    from stageproof.settings import ConfigError, load_settings
    settings = load_settings("config", env_path="nonexistent.env")
    assert settings.threshold("rain", "window_hours") == 48
    with pytest.raises(ConfigError, match=r"rain\.yes_mm"):
        settings.threshold("rain", "yes_mm")
    settings.attach_model({"thresholds": {"rain": {"yes_mm": 12.5}}})
    assert settings.threshold("rain", "yes_mm") == 12.5
    assert settings.threshold("rain", "window_hours") == 48  # yaml still wins


def test_parse_env_basic():
    from stageproof.settings import parse_env
    env = parse_env(
        "# comment\n"
        "STAGEPROOF_KEY_A=abc123\n"
        "export STAGEPROOF_KEY_B=\"quoted val\"\n"
        "\n"
        "EMPTY=\n"
    )
    assert env["STAGEPROOF_KEY_A"] == "abc123"
    assert env["STAGEPROOF_KEY_B"] == "quoted val"
    assert env["EMPTY"] == ""
