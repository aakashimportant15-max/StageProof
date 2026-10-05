"""Response layer: sensor state machines, alert machine, policy actions.

T026 — `SensorStateMachine` (RULES §7), one per role: A/T/C are driven only by
transport and basic health evidence; B is also driven by the verdict.
T027 — `AlertStateMachine` (RULES §8, §10.5, §11) with chained per-tick
transitions, retraction, estimate-based warning streaks and the conservative
clear basis; `best_level` per RULES §11.
T028 — `ActionPolicy` (RULES §12 trigger→actions map from config/policy.yaml,
tiers, demo_auto_approve_tier1, Tier 2 never auto-approved) and `Approvals`
(officer approve/reject with recorded identity).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from .domain import (Action, ActionStatus, ActionType, AlertState, Conf,
                     Context, Downstream, Estimate, Label, SensorState,
                     SensorStatus, Verdict)
from .settings import Policy, Thresholds

__all__ = ["Transition", "SensorStateMachine", "AlertStateMachine",
           "best_level", "ActionPolicy", "Approvals"]


@dataclass(frozen=True)
class Transition:
    """One logged state change (chained transitions are separate entries)."""
    entity: str
    from_state: str
    to_state: str
    reason: str
    source: Optional[str] = None


def _finite(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# T026 — sensor state machines (RULES §7)
# ---------------------------------------------------------------------------

class SensorStateMachine:
    """One machine per role. Inputs per tick: transport flags, health flags,
    and for B the decision-engine candidate/committed verdict. Counters follow
    RULES §7 exactly; recovery from an ATTACK quarantine additionally needs an
    officer ACK_RELEASE (`needs_ack`)."""

    def __init__(self, role: str, thresholds: Thresholds):
        self.role = role
        self._soft_ticks = int(thresholds.transport.soft_flag_suspect_ticks)
        self._fault_ticks = int(thresholds.persistence.fault_attack_ticks)
        self._downgrade = int(thresholds.persistence.real_downgrade_ticks)
        self._recover_after = int(thresholds.sensor.clean_ticks_to_recover)
        self._recovery_ticks = int(thresholds.sensor.recovery_ticks)
        self._immediate = tuple(thresholds.persistence.immediate_subtypes)
        self.state = SensorState.TRUSTED
        self.since_tick = 0
        self.reason: Optional[str] = None
        self.needs_ack = False
        self._clean = 0        # consecutive clean ticks
        self._soft = 0         # consecutive soft-flag ticks
        self._adverse = 0      # consecutive shape-flag or HARD-flag ticks
        self._rec = 0          # clean ticks counted while RECOVERING
        self.last_transitions: tuple[Transition, ...] = ()

    def status(self) -> SensorStatus:
        return SensorStatus(role=self.role, state=self.state,
                            since_tick=self.since_tick, reason=self.reason,
                            clean_streak=self._clean,
                            candidate_streak=self._adverse,
                            needs_ack=self.needs_ack)

    def ack_release(self) -> bool:
        """Officer ACK_RELEASE (Tier 2): clears the ATTACK release gate."""
        had = self.needs_ack
        self.needs_ack = False
        return had

    def update(self, tick_idx: int, *, soft: bool = False, hard: bool = False,
               shape: bool = False, shape_ok: bool = True,
               candidate: Optional[Label] = None,
               subtype: Optional[str] = None,
               verdict: Optional[Verdict] = None, notable: bool = False,
               context: Context = Context.INSUFFICIENT) -> SensorStatus:
        prev = self.state

        clean = (not soft) and (not hard) and (not shape) and shape_ok
        if self.role == "B":
            clean = clean and candidate in (Label.NORMAL, Label.REAL_FLOOD)
        self._clean = self._clean + 1 if clean else 0
        self._soft = self._soft + 1 if soft else 0
        self._adverse = self._adverse + 1 if (shape or hard) else 0

        commit = (verdict is not None and verdict.changed
                  and verdict.label in (Label.SENSOR_FAULT,
                                        Label.POSSIBLE_CYBER_ATTACK))
        immediate = commit and verdict is not None and \
            verdict.subtype in self._immediate
        attack_commit = commit and verdict is not None and \
            verdict.label == Label.POSSIBLE_CYBER_ATTACK

        # Relapse: any FAULT/ATTACK candidate for 1 tick while RECOVERING
        # (B). For A/T/C the analogous adverse condition relapses too.
        relapse = self.state == SensorState.RECOVERING and (
            (self.role == "B"
             and candidate in (Label.SENSOR_FAULT,
                               Label.POSSIBLE_CYBER_ATTACK))
            or (self.role != "B" and (shape or hard)))

        new_state = self.state
        reason = self.reason
        if relapse:
            new_state = SensorState.QUARANTINED
            reason = "relapse"
            self.needs_ack = self.role == "B" and \
                candidate == Label.POSSIBLE_CYBER_ATTACK
        elif self.state == SensorState.QUARANTINED:
            if (self._clean >= self._recover_after and not self.needs_ack
                    and shape_ok):
                new_state = SensorState.RECOVERING
                reason = "clean ticks reached"
                self._rec = 0
        elif self.state == SensorState.RECOVERING:
            # recovery_ticks = 8 FURTHER consecutive clean ticks in
            # RECOVERING (RULES §7); counted separately from the quarantine
            # clean streak so an ACK_RELEASE delay cannot shorten recovery.
            if clean:
                self._rec += 1
            else:
                self._rec = 0
            if self._rec >= self._recovery_ticks:
                new_state = SensorState.TRUSTED
                reason = "recovery complete"
        elif self.state == SensorState.SUSPECT:
            if commit:
                new_state = SensorState.QUARANTINED
                reason = f"verdict commit {verdict.label.value}"
                self.needs_ack = attack_commit
            elif self.role != "B" and self._adverse >= self._fault_ticks:
                new_state = SensorState.QUARANTINED
                reason = "health/transport flag persisted"
            elif self._clean >= self._downgrade:
                new_state = SensorState.TRUSTED
                reason = "clean candidates"
        elif self.state == SensorState.TRUSTED:
            if immediate:
                new_state = SensorState.QUARANTINED
                reason = f"immediate-subtype commit {verdict.subtype}"
                self.needs_ack = attack_commit
            elif commit:
                # Commits imply a persisted candidate whose first tick already
                # moved TRUSTED->SUSPECT; quarantining anyway is fail-safe.
                new_state = SensorState.QUARANTINED
                reason = f"verdict commit {verdict.label.value}"
                self.needs_ack = attack_commit
            elif self._soft >= self._soft_ticks:
                new_state = SensorState.SUSPECT
                reason = "consecutive soft transport flags"
            elif self.role == "B" and candidate in (
                    Label.SENSOR_FAULT, Label.POSSIBLE_CYBER_ATTACK):
                new_state = SensorState.SUSPECT
                reason = f"candidate {candidate.value if candidate else ''}"
            elif (self.role == "B" and candidate == Label.UNCERTAIN
                    and notable and context != Context.CONSISTENT):
                new_state = SensorState.SUSPECT
                reason = "notable UNCERTAIN, context inconsistent"
            elif self.role != "B" and shape:
                new_state = SensorState.SUSPECT
                reason = "non-physical health flag"

        self.last_transitions = ()
        if new_state != prev:
            self.state = new_state
            self.since_tick = tick_idx
            self.reason = reason
            self._clean = self._clean if new_state != \
                SensorState.QUARANTINED else 0
            self.last_transitions = (
                Transition(entity=f"sensor:{self.role}",
                           from_state=prev.value, to_state=new_state.value,
                           reason=reason or ""),)
        return self.status()


# ---------------------------------------------------------------------------
# T027 — alert state machine and best level (RULES §8, §10.5, §11)
# ---------------------------------------------------------------------------

def best_level(b_trusted: bool, obs_stage: Optional[float],
               estimate: Optional[Estimate]) -> tuple[Optional[float],
                                                      Optional[str]]:
    """RULES §11: observed stage while B is TRUSTED, otherwise the estimate
    (never an untrusted observed value; N-1)."""
    if b_trusted and _finite(obs_stage):
        return float(obs_stage), "OBSERVED"
    if estimate is not None and estimate.in_use and \
            _finite(estimate.stage_hat):
        return float(estimate.stage_hat), "ESTIMATE"
    return None, None


class AlertStateMachine:
    """One alert state for the reach. Several transitions may chain within one
    tick; each is returned as a separate `Transition`."""

    def __init__(self, thresholds: Thresholds, levels: dict):
        self._watch = float(levels["watch_stage"])
        self._action = float(levels["action_stage"])
        self._clear = float(levels["clear_stage"])
        self._idle_ticks = int(thresholds.alert.watch_idle_ticks)
        self._clear_ticks = int(thresholds.alert.clear_ticks)
        self._est_warn = int(thresholds.alert.est_warn_ticks)
        self.state = AlertState.NONE
        self.source: Optional[str] = None
        self.since_tick = 0
        self._idle = 0
        self._low = 0
        self._est = 0
        self._pending_confirm = False
        self._pending_clear = False
        self.last_transitions: tuple[Transition, ...] = ()

    def officer_confirm(self) -> None:
        self._pending_confirm = True

    def officer_clear(self) -> None:
        self._pending_clear = True

    def update(self, tick_idx: int, *, verdict_label: Label,
               notable: bool = False, best_level: Optional[float] = None,
               best_source: Optional[str] = None,
               obs_stage: Optional[float] = None, in_use: bool = False,
               estimate: Optional[Estimate] = None,
               downstream: Downstream = Downstream.UNKNOWN,
               community_confirmed: bool = False) -> list[Transition]:
        est_ok = (in_use and estimate is not None
                  and estimate.confidence in (Conf.HIGH, Conf.MED)
                  and _finite(estimate.stage_lo)
                  and float(estimate.stage_lo) >= self._action)
        self._est = self._est + 1 if est_ok else 0
        est_rule = est_ok and self._est >= self._est_warn

        est_watch = (in_use and estimate is not None
                     and _finite(estimate.stage_hi)
                     and float(estimate.stage_hi) >= self._action
                     and (not _finite(estimate.stage_lo)
                          or float(estimate.stage_lo) < self._action))

        committed_real = verdict_label == Label.REAL_FLOOD
        committed_unc_notable = verdict_label == Label.UNCERTAIN and notable
        committed_fault_attack = verdict_label in (
            Label.SENSOR_FAULT, Label.POSSIBLE_CYBER_ATTACK)

        level_ok = _finite(best_level)
        watch_trigger = (committed_unc_notable
                         or (committed_real and level_ok
                             and float(best_level) >= self._watch)
                         or est_watch)
        self._idle = 0 if watch_trigger else self._idle + 1

        prov_trigger = ((committed_real and _finite(obs_stage)
                         and float(obs_stage) >= self._action)
                        or est_rule
                        or (community_confirmed and notable))
        confirm_trigger = (downstream == Downstream.YES and level_ok
                           and float(best_level) >= self._action)
        retract = (committed_fault_attack and best_source == "OBSERVED"
                   and not est_rule)

        # clear_level: observed when OBSERVED, stage_hi when ESTIMATE
        # (conservative; RULES §8). Missing data never accumulates clear
        # streaks (N-16: a warning must not clear on lost data).
        clear_level: Optional[float] = None
        if best_source == "OBSERVED" and _finite(obs_stage):
            clear_level = float(obs_stage)
        elif best_source == "ESTIMATE" and estimate is not None and \
                _finite(estimate.stage_hi):
            clear_level = float(estimate.stage_hi)
        self._low = self._low + 1 if (clear_level is not None
                                      and clear_level < self._clear) else 0

        transitions: list[Transition] = []
        pending_confirm = self._pending_confirm
        pending_clear = self._pending_clear
        self._pending_confirm = False
        self._pending_clear = False

        for _ in range(4):
            prev = self.state
            nxt: Optional[AlertState] = None
            src: Optional[str] = None
            why = ""
            if prev == AlertState.NONE:
                if watch_trigger:
                    nxt = AlertState.WATCH
                    src = "ESTIMATE" if est_watch else "OBSERVED"
                    why = "watch trigger"
            elif prev == AlertState.WATCH:
                if prov_trigger:
                    nxt = AlertState.PROVISIONAL_WARNING
                    src = ("COMMUNITY" if community_confirmed
                           else "ESTIMATE" if est_rule else "OBSERVED")
                    why = "provisional trigger"
                elif self._idle >= self._idle_ticks:
                    nxt = AlertState.NONE
                    why = "watch idle"
            elif prev == AlertState.PROVISIONAL_WARNING:
                if pending_clear:
                    nxt = AlertState.CLEARED
                    src = "OFFICER"
                    why = "officer clear"
                elif pending_confirm or confirm_trigger:
                    nxt = AlertState.CONFIRMED_WARNING
                    src = "OFFICER" if pending_confirm else "DOWNSTREAM"
                    why = "confirmed"
                elif retract:
                    nxt = AlertState.WATCH
                    src = None
                    why = "retraction (basis invalidated)"
                elif self._low >= self._clear_ticks:
                    nxt = AlertState.CLEARED
                    why = "below clear level"
            elif prev == AlertState.CONFIRMED_WARNING:
                if pending_clear:
                    nxt = AlertState.CLEARED
                    src = "OFFICER"
                    why = "officer clear"
                elif self._low >= self._clear_ticks:
                    nxt = AlertState.CLEARED
                    why = "below clear level"
            elif prev == AlertState.CLEARED:
                if self.since_tick < tick_idx:   # back to NONE next tick only
                    nxt = AlertState.NONE
                    why = "post-clear"

            if nxt is None or nxt == prev:
                break
            self.state = nxt
            self.since_tick = tick_idx
            if src is not None:
                self.source = src
            transitions.append(Transition(entity="alert",
                                          from_state=prev.value,
                                          to_state=nxt.value, reason=why,
                                          source=src))
        self.last_transitions = tuple(transitions)
        return transitions


# ---------------------------------------------------------------------------
# T028 — policy, actions, approvals (RULES §12)
# ---------------------------------------------------------------------------

class ActionPolicy:
    """Creates actions from the config/policy.yaml trigger map. Tier 0 is
    automatic; Tier 1 is auto-approved only when demo_auto_approve_tier1;
    Tier 2 stays PENDING until a human call."""

    def __init__(self, policy: Policy, configured: Optional[dict] = None):
        self._policy = policy
        self._configured = dict(configured or {})
        self._counter = 0

    def create(self, trigger: str, ts: datetime, target: Optional[str] = None,
               payload: Optional[dict] = None) -> list[Action]:
        actions: list[Action] = []
        for entry in self._policy.trigger_actions(trigger):
            if isinstance(entry, dict):
                name = entry.get("action")
                cond = entry.get("if_configured")
                if cond and not self._configured.get(str(cond)):
                    continue
            else:
                name = entry
            if not name:
                continue
            tier = self._policy.tier(name)
            if tier == 0 or (tier == 1
                             and self._policy.demo_auto_approve_tier1):
                status = ActionStatus.AUTO_APPROVED
            else:
                status = ActionStatus.PENDING
            self._counter += 1
            act_payload = dict(payload or {})
            if isinstance(entry, dict):
                for key in ("level", "note"):
                    if entry.get(key) is not None:
                        act_payload[key] = entry[key]
            actions.append(Action(
                action_id=f"ACT-{self._counter:05d}", ts=ts,
                type=ActionType(name), tier=tier, status=status,
                target=target, payload=act_payload))
        return actions


class Approvals:
    """Officer approve/reject with recorded identity (RULES §12)."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, actions: Iterable[Action]) -> None:
        for action in actions:
            self._actions[action.action_id] = action

    def get(self, action_id: str) -> Action:
        if action_id not in self._actions:
            raise KeyError(f"unknown action_id '{action_id}'")
        return self._actions[action_id]

    def approve(self, action_id: str, officer: str) -> Action:
        action = self.get(action_id)
        if action.status != ActionStatus.PENDING:
            raise ValueError(
                f"action {action_id} is {action.status.value}, not PENDING")
        action.status = ActionStatus.APPROVED
        action.approver = officer
        return action

    def reject(self, action_id: str, officer: str) -> Action:
        action = self.get(action_id)
        if action.status != ActionStatus.PENDING:
            raise ValueError(
                f"action {action_id} is {action.status.value}, not PENDING")
        action.status = ActionStatus.REJECTED
        action.approver = officer
        return action

    def pending(self) -> tuple[Action, ...]:
        return tuple(a for a in self._actions.values()
                     if a.status == ActionStatus.PENDING)
