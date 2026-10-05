"""Runner core: the per-tick loop (ARCHITECTURE.md §4.1).

T031 implements steps 1-10 plus TickRecord assembly; T032 adds step 2
transport verification, steps 12-13 (audit-before-execute) and the
HALTED_AUDIT fail-closed path. T033 wires step 11 (sensor/alert machines,
policy actions, messages, community verification) and the operator methods
(approve/reject/ack_release/officer_confirm/officer_clear/replies), with
SENSOR_STATE/ALERT_STATE/ACTION/APPROVAL/VERIFICATION_REPLY/
ESTIMATE_IN_USE/SYSTEM_ERROR audit events. Model exceptions are contained
(ARCHITECTURE §14): the run continues on a degraded prediction.

Does not import `sim`. `replay_match` is false unless a ReplayArchive is
supplied (T020, P1).
"""

from __future__ import annotations

import hashlib
import math
from collections import deque
from pathlib import Path
from typing import Optional

from . import checks
from .checks import HealthResult, ReplayArchive
from .community import VerificationManager, basis_phrase, make_message, render
from .decision import VerdictTracker, decide
from .domain import (Action, ActionStatus, ActionType, AlertState, Context,
                     Label, Message, Prediction, SensorReading, SensorState,
                     SensorStatus, TickInput, TickRecord, Verdict,
                     VolunteerReply, Window, dataclass_to_dict)
from .evidence import EvidenceBuilder
from .estimator import make_estimate
from .models import (RAIN_HOURS, ModelBundle, RollingRobustZBaseline,
                     StaticThresholdBaseline, rain_accumulation)
from .response import (ActionPolicy, AlertStateMachine, Approvals,
                       SensorStateMachine, Transition, best_level)
from .security.audit import AuditError, AuditEvent, AuditLog, canonical_json
from .security.transport import TransportState, load_keys, verify
from .settings import Settings

__all__ = ["Runner", "TickOrderError"]


class TickOrderError(Exception):
    """Tick timestamps must be strictly increasing (ARCHITECTURE §4.1 step 1)."""


def _finite(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


class Runner:
    """Per-tick engine over one feed/scenario. Owns all mutable state."""

    def __init__(self, settings: Settings, bundle: ModelBundle,
                 audit_log: AuditLog,
                 replay_archive: Optional[ReplayArchive] = None):
        self.settings = settings
        self.bundle = bundle
        self.audit = audit_log
        self.replay_archive = replay_archive
        self.roles: tuple[str, ...] = settings.station_roles

        self._keys = load_keys(settings.env, settings.reach.stations)
        need = int(bundle.required_history_ticks)
        self._stage = {r: deque(maxlen=need) for r in self.roles}
        self._logq = {r: deque(maxlen=need) for r in self.roles}
        self._missing = {r: deque(maxlen=need) for r in self.roles}
        self._trust = {r: deque(maxlen=need) for r in self.roles}
        self._transport = {r: TransportState(r) for r in self.roles}
        self._status = {r: SensorStatus(role=r, state=SensorState.TRUSTED,
                                        since_tick=0) for r in self.roles}

        rain_hours = int(settings.threshold("rain", "window_hours"))
        self._rain_hourly: deque = deque(maxlen=rain_hours)
        stuck_ticks = int(settings.threshold("health", "stuck_ticks"))
        self._pred_logq: deque = deque(maxlen=max(stuck_ticks, 1))

        levels = settings.reach.levels["B"]
        self._eb = EvidenceBuilder(
            settings.thresholds, watch_stage=float(levels["watch_stage"]),
            tick_minutes=settings.reach.tick_minutes,
            lag_BC_ticks=bundle.lag_BC_ticks,
            yes_mm=settings.threshold("rain", "yes_mm"),
            no_mm=settings.threshold("rain", "no_mm"))
        self._tracker = VerdictTracker(settings.thresholds.persistence)
        bl = settings.reach.baselines
        self._static = StaticThresholdBaseline(float(levels["action_stage"]))
        self._rollz = RollingRobustZBaseline(
            int(bl.get("window_ticks", 96)),
            float(bl.get("rollz_threshold", 6.0)),
            float(settings.reach.station("B").get("quant_step", 0.01)))

        self._records: list[TickRecord] = []
        self._approved: list[Action] = []   # audited, then executed (N-5)

        # Step 11 response layer (T033).
        self._sensor = {r: SensorStateMachine(r, settings.thresholds)
                        for r in self.roles}
        self._alert = AlertStateMachine(settings.thresholds, levels)
        self._policy = ActionPolicy(
            settings.policy,
            configured={"evac_stage": levels.get("evac_stage")})
        self._approvals = Approvals()
        self._b_name = str(settings.reach.station("B").get("name", "B"))
        self._community = VerificationManager(settings, "B", self._b_name)
        self._unit = str(settings.reach.units.get("stage", "ft"))
        self._action_stage = float(levels["action_stage"])
        self._msg_n = 0
        self._community_used = False      # one COMMUNITY basis per episode
        self._warning_basis = "observed"  # basis of the live public warning
        self._in_use_prev = False
        self._pending_messages: list[Message] = []
        self._operator_events: list[dict] = []
        self._last_ts = None
        self._started = False
        self._finished = False
        self._halted = False
        self._model_hash = hashlib.sha256(
            canonical_json(bundle.artifact).encode("utf-8")).hexdigest()
        self._config_hash = self._hash_configs(settings.config_dir)

    @staticmethod
    def _hash_configs(config_dir: Path) -> str:
        sha = hashlib.sha256()
        for path in sorted(Path(config_dir).glob("*.yaml")):
            sha.update(path.name.encode("utf-8"))
            sha.update(path.read_bytes())
        return sha.hexdigest()

    # -- session lifecycle (T032) ------------------------------------------

    def start(self, scenario_id: str = "local", seed: int = 0,
              ts=None) -> None:
        if self._started:
            raise RuntimeError("Runner.start() called twice")
        self._started = True
        stamp = ts if ts is not None else self._last_ts
        if stamp is None:
            raise RuntimeError("start() needs the first tick ts (or a tick "
                               "already fed) — audit ts are simulated times")
        self._append(stamp, "SESSION_START",
                     {"scenario_id": scenario_id, "seed": int(seed),
                      "model_hash": self._model_hash,
                      "config_hash": self._config_hash})

    def finish(self) -> None:
        if not self._started:
            raise RuntimeError("Runner.finish() before start()")
        if self._finished:
            return
        if self._last_ts is None:
            raise RuntimeError("finish() with no ticks fed")
        self._finished = True
        self._append(self._last_ts, "SESSION_END",
                     {"ticks": len(self._records),
                      "head_hash": self.audit.head_hash})

    def _append(self, ts, kind: str, payload: dict) -> Optional[AuditEvent]:
        """Fail closed: an audit write error halts the run (T032)."""
        if self._halted:
            return None
        try:
            return self.audit.append(ts, kind, payload)
        except AuditError:
            self._halted = True
            return None

    def _execute_approved(self, ts,
                          out_events: Optional[list[AuditEvent]] = None
                          ) -> tuple[Action, ...]:
        """Step 13: audit-before-execute (N-5). Never runs while halted."""
        executed: list[Action] = []
        for action in self._approved:
            if action.status not in (ActionStatus.APPROVED,
                                     ActionStatus.AUTO_APPROVED):
                continue
            event = self._append(ts, "ACTION",
                                 {"action_id": action.action_id,
                                  "type": action.type.value,
                                  "target": action.target,
                                  "tier": action.tier})
            if event is not None and out_events is not None:
                out_events.append(event)
            if self._halted:
                break   # fail closed: no execution after a failed append
            action.status = ActionStatus.EXECUTED
            action.executed_ts = ts
            executed.append(action)
        self._approved = [a for a in self._approved
                          if a.status != ActionStatus.EXECUTED]
        return tuple(executed)

    # -- response-layer helpers (T033) ---------------------------------------

    def _next_msg_id(self) -> str:
        self._msg_n += 1
        return f"MSG-{self._msg_n:05d}"

    def _volunteer_lang(self, vid: str) -> str:
        vol = self.settings.messages.volunteers.get(vid) or {}
        return str(vol.get("language", "en"))

    def _reason_text(self, verdict: Verdict) -> str:
        texts = [self.settings.messages.reasons[code]
                 for code in verdict.reasons
                 if code in self.settings.messages.reasons]
        return "; ".join(texts) if texts else (verdict.subtype or "")

    def _auto_status(self, action: str) -> ActionStatus:
        tier = self.settings.policy.tier(action)
        if tier == 0 or (tier == 1
                         and self.settings.policy.demo_auto_approve_tier1):
            return ActionStatus.AUTO_APPROVED
        return ActionStatus.PENDING

    def _register_created(self, ts, actions: list[Action],
                          out_events: list[AuditEvent]) -> None:
        for act in actions:
            self._approvals.register([act])
            event = self._append(ts, "ACTION",
                                 {"stage": "created",
                                  "action_id": act.action_id,
                                  "type": act.type.value, "tier": act.tier,
                                  "status": act.status.value,
                                  "target": act.target})
            if event is not None:
                out_events.append(event)
            self._approved.append(act)

    def _message_for(self, act: Action, ts) -> Optional[Message]:
        """Rendered message for an action, or None when messages.yaml has no
        template for it (NOTIFY_OFFICER text is rendered once per verdict
        commit instead; DESIGN §9.3)."""
        p = act.payload or {}
        key: Optional[str] = None
        fields: dict = {}
        channel, audience = "SMS", "public"
        if act.type == ActionType.SEND_PUBLIC_WARNING:
            key = ("warning_provisional" if p.get("level") == "PROVISIONAL"
                   else "warning_confirmed")
            fields = {"station": self._b_name,
                      "level": str(p.get("level_text", "")),
                      "basis": basis_phrase(self.settings.messages,
                                            str(p.get("basis_key",
                                                      "observed")))}
        elif act.type == ActionType.SEND_CORRECTION:
            key = "correction"
            fields = {"station": self._b_name,
                      "status": self._alert.state.value}
        elif act.type == ActionType.SEND_ALL_CLEAR:
            key = "all_clear"
            fields = {"station": self._b_name}
        elif act.type == ActionType.RECOMMEND_EVACUATION:
            key = "officer_recommend_evac"
            channel = audience = "OFFICER"
            fields = {"station": self._b_name}
        if key is None:
            return None
        return make_message(self._next_msg_id(), ts, self.settings.messages,
                            key, channel=channel, audience=audience,
                            fields=fields, action_id=act.action_id)

    # -- operator actions between ticks (T033; audit before execute, N-5) ----

    def _require_active(self) -> None:
        if not self._started or self._finished:
            raise RuntimeError(
                "operator calls are allowed between start() and finish()")

    def pending_actions(self) -> tuple[Action, ...]:
        return self._approvals.pending()

    def approve(self, action_id: str, officer: str) -> Action:
        """Officer approval of a PENDING action; executes immediately after
        the APPROVAL audit event (N-5)."""
        self._require_active()
        action = self._approvals.approve(action_id, officer)
        self._append(self._last_ts, "APPROVAL",
                     {"action_id": action_id, "decision": "APPROVED",
                      "officer": officer})
        executed = self._execute_approved(self._last_ts)
        self._operator_events.append(
            {"kind": "APPROVAL", "action_id": action_id,
             "decision": "APPROVED", "officer": officer,
             "executed": [a.action_id for a in executed]})
        return action

    def reject(self, action_id: str, officer: str) -> Action:
        self._require_active()
        action = self._approvals.reject(action_id, officer)
        self._append(self._last_ts, "APPROVAL",
                     {"action_id": action_id, "decision": "REJECTED",
                      "officer": officer})
        self._operator_events.append(
            {"kind": "APPROVAL", "action_id": action_id,
             "decision": "REJECTED", "officer": officer, "executed": []})
        return action

    def ack_release(self, officer: str) -> Action:
        """Tier-2 ACK_RELEASE: the officer's human decision clears the ATTACK
        release gate; recovery still needs the clean condition (RULES §7)."""
        self._require_active()
        actions = self._policy.create("officer_ack_release", self._last_ts,
                                      target="B")
        act = actions[0]
        self._register_created(self._last_ts, actions, [])
        self._approvals.approve(act.action_id, officer)
        self._append(self._last_ts, "APPROVAL",
                     {"action_id": act.action_id, "decision": "APPROVED",
                      "officer": officer})
        released = False
        if not self._halted:
            released = self._sensor["B"].ack_release()
        executed = self._execute_approved(self._last_ts)
        self._operator_events.append(
            {"kind": "ACK_RELEASE", "officer": officer,
             "action_id": act.action_id, "gate_was_set": released,
             "executed": [a.action_id for a in executed]})
        return act

    def officer_confirm(self) -> None:
        self._require_active()
        self._alert.officer_confirm()
        self._operator_events.append({"kind": "OFFICER_CONFIRM"})

    def officer_clear(self) -> None:
        self._require_active()
        self._alert.officer_clear()
        self._operator_events.append({"kind": "OFFICER_CLEAR"})

    def submit_verification_reply(self, reply: VolunteerReply) -> \
            tuple[bool, str]:
        """Volunteer reply between ticks: audited (VERIFICATION_REPLY), the
        round posterior updates, a thanks message queues for the volunteer.
        Replies never release a quarantine (N-12)."""
        self._require_active()
        accepted, detail = self._community.submit_reply(reply)
        self._append(reply.ts, "VERIFICATION_REPLY",
                     {"round_id": reply.round_id,
                      "volunteer_id": reply.volunteer_id, "code": reply.code,
                      "accepted": accepted, "detail": detail})
        if accepted:
            lang = self._volunteer_lang(reply.volunteer_id)
            self._pending_messages.append(Message(
                msg_id=self._next_msg_id(), ts=reply.ts, channel="SMS",
                audience="VOLUNTEER", language=lang,
                text=render(self.settings.messages, "volunteer_thanks", lang),
                recipient_id=reply.volunteer_id))
        self._operator_events.append(
            {"kind": "VERIFICATION_REPLY", "volunteer_id": reply.volunteer_id,
             "code": reply.code, "accepted": accepted, "detail": detail})
        return accepted, detail

    # -- per-tick loop -------------------------------------------------------

    def step(self, tick: TickInput) -> TickRecord:
        if not self._started:
            raise RuntimeError("call start() before step()")
        # Step 1 — strict tick order; state unchanged on rejection.
        if self._last_ts is not None and tick.ts <= self._last_ts:
            raise TickOrderError(
                f"tick ts {tick.ts.isoformat()} is not after previous tick "
                f"{self._last_ts.isoformat()}")
        self._last_ts = tick.ts

        # Step 2 — transport verification; HARD flag rejects the packet.
        ts_skew = self.settings.thresholds.transport.ts_skew_max_seconds
        accepted: dict[str, bool] = {}
        tflags: dict[str, tuple[str, ...]] = {}
        thard: dict[str, tuple[str, ...]] = {}
        transport_failures: list[tuple[str, object]] = []
        for role in self.roles:
            reading = tick.readings.get(role)
            if reading is None:
                accepted[role] = False
                tflags[role] = ("MISSING",)
                thard[role] = ()
                continue
            verdict = verify(self._transport[role], reading, role, tick.ts,
                             self._keys, ts_skew)
            accepted[role] = verdict.accepted
            tflags[role] = verdict.flags
            thard[role] = verdict.hard
            if verdict.hard:
                transport_failures.append((role, verdict))

        # Step 3 — normalize accepted readings into ring buffers.
        for role in self.roles:
            reading = tick.readings.get(role)
            stage = logq = float("nan")
            if accepted.get(role) and _finite(reading.stage):
                try:
                    logq = math.log(self.bundle.q_from_stage(
                        role, float(reading.stage)))
                    stage = float(reading.stage)
                except Exception:
                    stage = logq = float("nan")
            self._stage[role].append(stage)
            self._logq[role].append(logq)
            self._missing[role].append(not math.isfinite(stage))
            self._trust[role].append(
                self._status[role].state == SensorState.TRUSTED)
        if tick.ts.minute == 0:
            rain = tick.rain_prev_hr_mm if tick.rain_feed_ok else None
            self._rain_hourly.append(
                float(rain) if _finite(rain) else float("nan"))

        # Steps 4-5 — trust map and the immutable window (nothing future).
        trust = {r: bool(self._trust[r][-1]) for r in self.roles}
        stage = {r: tuple(self._stage[r]) for r in self.roles}
        logq = {r: tuple(self._logq[r]) for r in self.roles}
        missing = {r: bool(self._missing[r][-1]) for r in self.roles}
        hourly = tuple(self._rain_hourly)
        rain_acc = {}
        for hours in RAIN_HOURS:
            acc = rain_accumulation(hourly, hours)
            if acc is not None:
                rain_acc[hours] = acc
        window = Window(ts=tick.ts, tick_idx=tick.tick_idx, stage=stage,
                        logq=logq, missing=missing, trust=trust,
                        rain_hourly=hourly, rain_acc=rain_acc,
                        rain_available=bool(tick.rain_feed_ok))

        # Step 6 — health checks (pred_change from past predictions only).
        pred_change: Optional[float] = None
        hist = list(self._pred_logq)
        if (self._pred_logq.maxlen is not None
                and len(hist) == self._pred_logq.maxlen
                and _finite(hist[0]) and _finite(hist[-1])):
            pred_change = abs(float(hist[-1]) - float(hist[0]))
        health: dict[str, HealthResult] = {}
        for role in self.roles:
            entry = self.settings.reach.station(role)
            health[role] = checks.health_checks(
                role, window, self.settings.thresholds.health,
                self.bundle.station_params.get(role, {}),
                float(entry["sensor_min"]), float(entry["sensor_max"]),
                pred_change=pred_change)

        # Step 7 — models, then baselines. Model exceptions are contained
        # (ARCHITECTURE §14): the run continues on a degraded prediction and
        # a SYSTEM_ERROR event is appended; the estimator falls back to an
        # unusable Estimate automatically.
        new_events: list[AuditEvent] = []
        try:
            prediction = self.bundle.predict_target(window, trust)
        except Exception as exc:
            prediction = Prediction(role="B", ts=tick.ts, variant="NONE",
                                    degraded=True, error=repr(exc))
            event = self._append(tick.ts, "SYSTEM_ERROR",
                                 {"component": "models.predict_target",
                                  "error": repr(exc)})
            if event is not None:
                new_events.append(event)
        try:
            downstream = self.bundle.predict_downstream(window)
        except Exception as exc:
            downstream = Prediction(role="C", ts=tick.ts, variant="NONE",
                                    degraded=True, error=repr(exc))
            event = self._append(tick.ts, "SYSTEM_ERROR",
                                 {"component": "models.predict_downstream",
                                  "error": repr(exc)})
            if event is not None:
                new_events.append(event)
        if _finite(prediction.logq_pred):
            self._pred_logq.append(float(prediction.logq_pred))
        b_stage = float(window.stage["B"][-1])
        threshold_alert = self._static.update(
            b_stage if math.isfinite(b_stage) else None)
        rollz_flag, rollz_value = self._rollz.update(
            b_stage if math.isfinite(b_stage) else None)
        baselines = {"threshold_alert": threshold_alert,
                     "rollz_value": rollz_value,
                     "rollz_flag": rollz_flag}

        # Step 8 — evidence (z histories live in the builder).
        replay_match = False
        if self.replay_archive is not None:
            recent = list(self._stage["B"])[-self.replay_archive.cfg.window_ticks:]
            replay_match = self.replay_archive.match(recent)
        evidence = self._eb.build(window, prediction, downstream,
                                  tflags, thard, health,
                                  replay_match=replay_match)

        # Step 9 — decision and persistence tracker.
        b_state = self._status["B"].state
        b_trusted = b_state == SensorState.TRUSTED
        candidate = decide(evidence, b_state)
        verdict = self._tracker.update(tick.ts, tick.tick_idx, candidate)

        # Step 10 — estimate (RULES §10.6 in_use policy).
        in_use = b_state in (SensorState.QUARANTINED, SensorState.RECOVERING)
        if not in_use and b_state == SensorState.SUSPECT:
            in_use = candidate.label in (Label.SENSOR_FAULT,
                                         Label.POSSIBLE_CYBER_ATTACK) or (
                candidate.label == Label.UNCERTAIN
                and evidence.context != Context.CONSISTENT)
        estimate = make_estimate(prediction, self.bundle, in_use,
                                 self.settings)

        # Step 12 — audit events (T032): TRANSPORT_FAILURE, VERDICT_CHANGE.
        for role, fail in transport_failures:
            event = self._append(tick.ts, "TRANSPORT_FAILURE",
                                 {"station": role, "flags": list(fail.hard),
                                  "detail": dict(fail.detail)})
            if event is not None:
                new_events.append(event)
            if self._halted:
                break
        if not self._halted and verdict.changed:
            event = self._append(tick.ts, "VERDICT_CHANGE",
                                 {"tick_idx": tick.tick_idx,
                                  "label": verdict.label.value,
                                  "subtype": verdict.subtype,
                                  "rule_id": verdict.rule_id,
                                  "confidence": verdict.confidence.value,
                                  "evidence": dataclass_to_dict(evidence)})
            if event is not None:
                new_events.append(event)

        # Step 11 — response layer (T033): sensor/alert machines, best
        # level, policy actions, messages, community verification.
        best_lvl, best_src = best_level(b_trusted, evidence.obs_stage,
                                        estimate)
        sensor_transitions: list[Transition] = []
        for role in self.roles:
            machine = self._sensor[role]
            is_b = role == "B"
            status = machine.update(
                tick.tick_idx,
                soft=bool(tflags[role]) and not thard[role],
                hard=bool(thard[role]),
                shape=not health[role].shape_ok,
                shape_ok=health[role].shape_ok,
                candidate=verdict.candidate if is_b else None,
                subtype=verdict.candidate_subtype if is_b else None,
                verdict=verdict if is_b else None,
                notable=evidence.notable if is_b else False,
                context=evidence.context if is_b else Context.INSUFFICIENT)
            self._status[role] = status
            sensor_transitions.extend(machine.last_transitions)

        community_confirmed = (self._community.confirmed
                               and not self._community_used)
        alert_transitions = self._alert.update(
            tick.tick_idx, verdict_label=verdict.label,
            notable=evidence.notable, best_level=best_lvl,
            best_source=best_src, obs_stage=evidence.obs_stage,
            in_use=estimate.in_use, estimate=estimate,
            downstream=evidence.downstream,
            community_confirmed=community_confirmed)
        for tr in alert_transitions:
            if tr.to_state == AlertState.PROVISIONAL_WARNING.value:
                if tr.source == "COMMUNITY":
                    self._community_used = True
                if tr.source:
                    self._warning_basis = tr.source.lower()
            elif tr.to_state == AlertState.CLEARED.value:
                self._warning_basis = "observed"

        if estimate.in_use != self._in_use_prev:
            event = self._append(tick.ts, "ESTIMATE_IN_USE",
                                 {"tick_idx": tick.tick_idx,
                                  "in_use": estimate.in_use,
                                  "variant": estimate.variant,
                                  "confidence": estimate.confidence.value})
            self._in_use_prev = estimate.in_use
            if event is not None:
                new_events.append(event)

        # Policy actions (RULES §12): verdict-commit triggers first, then
        # alert-transition triggers. The officer notice renders once per
        # commit; NOTIFY_OFFICER actions from alert_watch/alert_retraction/
        # verification timeouts carry no rendered text (DESIGN §9.3 defines
        # officer templates only for the uncertain/security/fault/evac cases).
        officer_msg: Optional[Message] = None
        created: list[Action] = []
        if verdict.changed:
            if verdict.label == Label.SENSOR_FAULT:
                officer_msg = make_message(
                    self._next_msg_id(), tick.ts, self.settings.messages,
                    "officer_fault", channel="OFFICER", audience="OFFICER",
                    fields={"sensor": "B", "subtype": verdict.subtype or ""})
                created.extend(self._policy.create(
                    "verdict_sensor_fault", tick.ts, target="B"))
            elif verdict.label == Label.POSSIBLE_CYBER_ATTACK:
                officer_msg = make_message(
                    self._next_msg_id(), tick.ts, self.settings.messages,
                    "officer_security", channel="OFFICER", audience="OFFICER",
                    fields={"sensor": "B", "reason": self._reason_text(verdict)})
                created.extend(self._policy.create(
                    "verdict_possible_cyber_attack", tick.ts, target="B"))
            elif verdict.label == Label.UNCERTAIN and evidence.notable:
                officer_msg = make_message(
                    self._next_msg_id(), tick.ts, self.settings.messages,
                    "officer_uncertain", channel="OFFICER", audience="OFFICER",
                    fields={"station": "B",
                            "reason": self._reason_text(verdict)})
                created.extend(self._policy.create(
                    "verdict_uncertain_notable", tick.ts, target="B"))
        level_text = (f"{best_lvl:.1f} {self._unit}" if _finite(best_lvl)
                      else f"{self._action_stage:.1f} {self._unit}")
        for tr in alert_transitions:
            if tr.from_state == AlertState.NONE.value \
                    and tr.to_state == AlertState.WATCH.value:
                created.extend(self._policy.create(
                    "alert_watch", tick.ts, target="B"))
            elif tr.to_state == AlertState.PROVISIONAL_WARNING.value:
                created.extend(self._policy.create(
                    "alert_provisional_warning", tick.ts, target="B",
                    payload={"level": "PROVISIONAL",
                             "level_text": level_text,
                             "basis_key": self._warning_basis}))
            elif tr.to_state == AlertState.CONFIRMED_WARNING.value:
                created.extend(self._policy.create(
                    "alert_confirmed_warning", tick.ts, target="B",
                    payload={"level": "CONFIRMED",
                             "level_text": level_text,
                             "basis_key": ("downstream"
                                           if tr.source == "DOWNSTREAM"
                                           else self._warning_basis)}))
            elif tr.reason.startswith("retraction"):
                created.extend(self._policy.create(
                    "alert_retraction", tick.ts, target="B"))
            elif tr.to_state == AlertState.CLEARED.value:
                created.extend(self._policy.create(
                    "alert_cleared", tick.ts, target="B"))
        self._register_created(tick.ts, created, new_events)
        for act in created:
            msg = self._message_for(act, tick.ts)
            if msg is not None:
                self._pending_messages.append(msg)
        if officer_msg is not None:
            self._pending_messages.append(officer_msg)

        for tr in sensor_transitions:
            event = self._append(tick.ts, "SENSOR_STATE",
                                 {"entity": tr.entity,
                                  "from_state": tr.from_state,
                                  "to_state": tr.to_state,
                                  "reason": tr.reason})
            if event is not None:
                new_events.append(event)
        for tr in alert_transitions:
            event = self._append(tick.ts, "ALERT_STATE",
                                 {"entity": "alert",
                                  "from_state": tr.from_state,
                                  "to_state": tr.to_state,
                                  "reason": tr.reason,
                                  "source": tr.source})
            if event is not None:
                new_events.append(event)

        # Community verification rounds (RULES §13); replies never release a
        # quarantine (N-12) — only the officer ACK_RELEASE does.
        rnd = self._community.maybe_open(
            tick.tick_idx, tick.ts,
            uncertain_notable=(verdict.changed
                               and verdict.label == Label.UNCERTAIN
                               and evidence.notable))
        if rnd is not None:
            for vid, text in rnd.prompts.items():
                self._pending_messages.append(Message(
                    msg_id=self._next_msg_id(), ts=tick.ts, channel="SMS",
                    audience="VOLUNTEER",
                    language=self._volunteer_lang(vid), text=text,
                    recipient_id=vid))
        timed_out = self._community.tick(tick.tick_idx, tick.ts)
        if timed_out is not None:
            act = Action(
                action_id=f"ACT-VRT-{timed_out.round_id}", ts=tick.ts,
                type=ActionType.NOTIFY_OFFICER,
                tier=self.settings.policy.tier("NOTIFY_OFFICER"),
                status=self._auto_status("NOTIFY_OFFICER"), target="B",
                payload={"reason": (f"verification round "
                                    f"{timed_out.round_id} timed out"),
                         "status": self._alert.state.value})
            self._register_created(tick.ts, [act], new_events)

        # Step 13 — execute only actions whose audit append succeeded.
        executed = self._execute_approved(tick.ts, new_events)

        record = TickRecord(
            tick_idx=tick.tick_idx, ts=tick.ts,
            readings=dict(tick.readings), accepted=accepted,
            transport_flags=tflags, prediction=prediction,
            downstream_prediction=downstream, evidence=evidence,
            verdict=verdict, estimate=estimate,
            sensor_status=dict(self._status),
            alert_state=self._alert.state, alert_source=self._alert.source,
            best_level=best_lvl, best_source=best_src,
            new_actions=executed,
            new_messages=tuple(self._pending_messages),
            verification=self._community.summary(),
            baselines=baselines, audit_events_new=tuple(new_events),
            audit_head_hash=self.audit.head_hash,
            operator_events=tuple(self._operator_events),
            halted_audit=self._halted, truth=None)
        self._pending_messages = []
        self._operator_events = []
        self._records.append(record)
        return record
