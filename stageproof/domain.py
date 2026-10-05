"""Domain vocabulary for StageProof (ARCHITECTURE.md §11).

Enums and frozen-where-practical dataclasses shared by every layer. This module
imports nothing from other package modules (T005 acceptance).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union, get_args, get_origin, get_type_hints

__all__ = [
    "Label", "SensorState", "AlertState", "Context", "Trend", "Support",
    "Downstream", "Conf", "ActionType", "ActionStatus",
    "SensorReading", "TickInput", "TruthTick", "Window", "Prediction",
    "Evidence", "Verdict", "SensorStatus", "Estimate", "Action", "Message",
    "VolunteerReply", "AuditEvent", "Scenario", "TickRecord",
    "record_to_dict", "record_from_dict", "dataclass_to_dict", "dataclass_from_dict",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Label(str, Enum):
    REAL_FLOOD = "REAL_FLOOD"
    SENSOR_FAULT = "SENSOR_FAULT"
    POSSIBLE_CYBER_ATTACK = "POSSIBLE_CYBER_ATTACK"
    UNCERTAIN = "UNCERTAIN"
    NORMAL = "NORMAL"


class SensorState(str, Enum):
    TRUSTED = "TRUSTED"
    SUSPECT = "SUSPECT"
    QUARANTINED = "QUARANTINED"
    RECOVERING = "RECOVERING"


class AlertState(str, Enum):
    NONE = "NONE"
    WATCH = "WATCH"
    PROVISIONAL_WARNING = "PROVISIONAL_WARNING"
    CONFIRMED_WARNING = "CONFIRMED_WARNING"
    CLEARED = "CLEARED"


class Context(str, Enum):
    CONSISTENT = "CONSISTENT"
    PHANTOM = "PHANTOM"
    SUPPRESSED = "SUPPRESSED"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT = "INSUFFICIENT"


class Trend(str, Enum):
    RISING = "RISING"
    FLAT = "FLAT"
    FALLING = "FALLING"
    UNKNOWN = "UNKNOWN"


class Support(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


class Downstream(str, Enum):
    YES = "YES"
    NO = "NO"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class Conf(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"


class ActionType(str, Enum):
    QUARANTINE_SENSOR = "QUARANTINE_SENSOR"
    USE_ESTIMATE = "USE_ESTIMATE"
    OPEN_MAINTENANCE_TICKET = "OPEN_MAINTENANCE_TICKET"
    RAISE_SECURITY_ALERT = "RAISE_SECURITY_ALERT"
    PRESERVE_EVIDENCE = "PRESERVE_EVIDENCE"
    NOTIFY_OFFICER = "NOTIFY_OFFICER"
    REQUEST_VERIFICATION = "REQUEST_VERIFICATION"
    SEND_PUBLIC_WARNING = "SEND_PUBLIC_WARNING"
    SEND_CORRECTION = "SEND_CORRECTION"
    SEND_ALL_CLEAR = "SEND_ALL_CLEAR"
    RECOMMEND_EVACUATION = "RECOMMEND_EVACUATION"
    ACK_RELEASE = "ACK_RELEASE"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    AUTO_APPROVED = "AUTO_APPROVED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SensorReading:
    station_id: str
    ts: datetime
    seq: int
    stage: float
    unit: str
    sig: str


@dataclass(frozen=True)
class TickInput:
    """One stream tick handed to Runner.step. Contains no scenario truth."""
    ts: datetime
    tick_idx: int
    readings: dict[str, Optional[SensorReading]] = field(default_factory=dict)
    rain_prev_hr_mm: Optional[float] = None
    rain_feed_ok: bool = True


@dataclass(frozen=True)
class TruthTick:
    """Ground truth for evaluation only; never enters the engine."""
    ts: datetime
    true_q_B: Optional[float]
    truth_label: str
    true_stage: dict[str, float] = field(default_factory=dict)
    corrupted_roles: tuple[str, ...] = ()
    truth_notable: bool = False


@dataclass(frozen=True)
class Window:
    """Bounded per-tick input window; ends at the current tick (no future data)."""
    ts: datetime
    tick_idx: int
    stage: dict[str, tuple[float, ...]] = field(default_factory=dict)
    logq: dict[str, tuple[float, ...]] = field(default_factory=dict)
    missing: dict[str, bool] = field(default_factory=dict)
    trust: dict[str, bool] = field(default_factory=dict)
    rain_hourly: tuple[float, ...] = ()
    rain_acc: dict[int, float] = field(default_factory=dict)
    rain_available: bool = False


@dataclass(frozen=True)
class Prediction:
    role: str
    ts: datetime
    variant: str
    inputs_used: tuple[str, ...] = ()
    logq_obs: Optional[float] = None
    logq_pred: Optional[float] = None
    scale: Optional[float] = None
    regime: str = "normal"
    z: Optional[float] = None
    q_pred: Optional[float] = None
    stage_pred: Optional[float] = None
    degraded: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class Evidence:
    """Typed facts for one tick; no labels (facts only)."""
    ts: datetime
    tick_idx: int
    transport_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    transport_hard: dict[str, tuple[str, ...]] = field(default_factory=dict)
    health_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    shape_ok: Optional[bool] = None
    rate_exceeded: bool = False
    variant: Optional[str] = None
    z: Optional[float] = None
    z_mean: Optional[float] = None
    context: Context = Context.INSUFFICIENT
    up_trend: Trend = Trend.UNKNOWN
    rain: Support = Support.UNKNOWN
    downstream: Downstream = Downstream.UNKNOWN
    z_down_mean: Optional[float] = None
    replay_match: bool = False
    noise_too_clean: bool = False
    drift: bool = False
    obs_stage: Optional[float] = None
    pred_stage: Optional[float] = None
    notable: bool = False
    degraded: bool = False
    trusted_inputs: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    ts: datetime
    tick_idx: int
    candidate: Label
    candidate_subtype: Optional[str] = None
    label: Label = Label.NORMAL
    subtype: Optional[str] = None
    confidence: Conf = Conf.NONE
    persist_count: int = 0
    persist_needed: int = 0
    rule_id: Optional[str] = None
    reasons: tuple[str, ...] = ()
    changed: bool = False


@dataclass(frozen=True)
class SensorStatus:
    role: str
    state: SensorState
    since_tick: int
    reason: Optional[str] = None
    clean_streak: int = 0
    candidate_streak: int = 0
    needs_ack: bool = False


@dataclass(frozen=True)
class Estimate:
    ts: datetime
    stage_hat: Optional[float] = None
    stage_lo: Optional[float] = None
    stage_hi: Optional[float] = None
    q_hat: Optional[float] = None
    variant: str = ""
    basis: tuple[str, ...] = ()
    confidence: Conf = Conf.NONE
    in_use: bool = False


@dataclass
class Action:
    """Mutable: status progresses PENDING -> ... -> EXECUTED after audit (N-5)."""
    action_id: str
    ts: datetime
    type: ActionType
    tier: int
    status: ActionStatus
    target: Optional[str] = None
    payload: dict = field(default_factory=dict)
    approver: Optional[str] = None
    executed_ts: Optional[datetime] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class Message:
    msg_id: str
    ts: datetime
    channel: str        # SMS | IVR | OFFICER | TICKET
    audience: str
    language: str
    text: str
    action_id: Optional[str] = None
    recipient_id: Optional[str] = None


@dataclass(frozen=True)
class VolunteerReply:
    ts: datetime
    volunteer_id: str
    code: int           # 1 = above mark, 2 = not above, 3 = not sure
    round_id: str


@dataclass(frozen=True)
class AuditEvent:
    idx: int
    ts: datetime
    kind: str
    payload: dict
    prev_hash: str
    hash: str


@dataclass(frozen=True)
class Scenario:
    """Manifest data (ARCHITECTURE.md §9.2); structured sub-objects stay dicts."""
    id: str
    title: str
    description: str
    seed: int
    window: dict                 # {start, end} UTC ISO
    warmup_ticks: int
    event_start_tick: int
    corruptions: tuple[dict, ...] = ()
    compromised_keys: tuple[str, ...] = ()
    transport_attacks: tuple[dict, ...] = ()
    feed_outages: tuple[dict, ...] = ()
    volunteer_script: tuple[dict, ...] = ()
    operator_script: tuple[dict, ...] = ()
    ground_truth: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)


@dataclass
class TickRecord:
    """Complete per-tick output consumed by the dashboard and CLI."""
    tick_idx: int
    ts: datetime
    readings: dict[str, Optional[SensorReading]] = field(default_factory=dict)
    accepted: dict[str, bool] = field(default_factory=dict)
    transport_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    prediction: Optional[Prediction] = None
    downstream_prediction: Optional[Prediction] = None
    evidence: Optional[Evidence] = None
    verdict: Optional[Verdict] = None
    estimate: Optional[Estimate] = None
    sensor_status: dict[str, SensorStatus] = field(default_factory=dict)
    alert_state: AlertState = AlertState.NONE
    alert_source: Optional[str] = None
    best_level: Optional[float] = None
    best_source: Optional[str] = None
    new_actions: tuple[Action, ...] = ()
    new_messages: tuple[Message, ...] = ()
    verification: Optional[dict] = None                  # verification-round summary
    baselines: Optional[dict] = None                     # threshold_alert, rollz_value, rollz_flag
    audit_events_new: tuple[AuditEvent, ...] = ()
    audit_head_hash: Optional[str] = None
    operator_events: tuple[dict, ...] = ()
    halted_audit: bool = False
    truth: Optional[TruthTick] = None                    # attached by the driver only


# ---------------------------------------------------------------------------
# Serialization: record_to_dict / record_from_dict (enums as strings,
# datetimes ISO; tuples round-trip via annotations). The codec is driven by
# dataclass type hints, so every field of every §11 class round-trips exactly.
# ---------------------------------------------------------------------------

def _encode(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    raise TypeError(f"cannot encode {type(value)!r}")


def _decode(value: Any, tp: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin is Union:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return _decode(value, args[0])
        # Union of concrete types: pick the member the value matches.
        for arg in args:
            arg_origin = get_origin(arg)
            if isinstance(value, dict):
                if (is_dataclass(arg) and not isinstance(arg, type)
                        and (arg_origin is None or arg_origin is dict)):
                    return _decode(value, arg)
            elif arg is datetime and isinstance(value, str):
                return _decode(value, arg)
            elif (isinstance(arg, type) and issubclass(arg, Enum)
                  and isinstance(value, str)):
                return _decode(value, arg)
            elif arg is float and isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            elif arg is int and isinstance(value, int) and not isinstance(value, bool):
                return int(value)
            elif arg is str and isinstance(value, str):
                return str(value)
        raise TypeError(f"cannot decode {value!r} as {tp!r}")
    if tp is Any or tp is None:
        return value
    if tp is datetime:
        return datetime.fromisoformat(value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if is_dataclass(tp) and isinstance(value, dict):
        hints = get_type_hints(tp)
        kwargs = {f.name: _decode(value[f.name], hints[f.name])
                  for f in fields(tp) if f.name in value}
        return tp(**kwargs)
    if origin in (list, tuple):
        args = get_args(tp)
        arg = args[0] if args else Any
        decoded = [_decode(v, arg) for v in value]
        return tuple(decoded) if origin is tuple else decoded
    if origin is dict:
        key_tp, val_tp = get_args(tp)
        return {_decode(k, key_tp): _decode(v, val_tp) for k, v in value.items()}
    if tp is float:
        return float(value)
    if tp is int:
        return int(value)
    if tp is bool:
        return bool(value)
    if tp is str:
        return str(value)
    if tp is dict:
        return value
    if tp is tuple:
        return tuple(value)
    raise TypeError(f"cannot decode {value!r} as {tp!r}")


def record_to_dict(record: TickRecord) -> dict:
    """Encode a TickRecord (or any §11 dataclass) to a JSON-safe dict."""
    return _encode(record)


def record_from_dict(data: dict) -> TickRecord:
    """Rebuild a TickRecord from record_to_dict output (exact round-trip)."""
    return dataclass_from_dict(data, TickRecord)


def dataclass_from_dict(data: dict, tp: Any) -> Any:
    """Rebuild any §11 dataclass from dataclass_to_dict output."""
    return _decode(data, tp)


def dataclass_to_dict(obj: Any) -> dict:
    """Encode any §11 dataclass (Scenario, Evidence, ...) to a JSON-safe dict."""
    return _encode(obj)
