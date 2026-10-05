"""Decision engine (T022-T023; RULES §4 table + post-adjustments, §6
persistence/hysteresis).

`decide` is a pure function of the tick's Evidence and B's sensor state —
no I/O, no clock, no scenario knowledge. `VerdictTracker` converts the
per-tick candidate into the committed verdict with hysteresis. Imports only
domain and settings (ARCHITECTURE §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .domain import Conf, Context, Downstream, Evidence, Label, SensorState, \
    Support, Trend, Verdict
from .settings import PersistenceThresholds

__all__ = ["Candidate", "decide", "VerdictTracker"]

_ATTACK = Label.POSSIBLE_CYBER_ATTACK
_FAULT = Label.SENSOR_FAULT

# RULES §4 R2: subtype priority among active shape checks.
_FAULT_PRIORITY = ("RANGE", "SPIKE", "STUCK", "DROPOUT", "NOISE")
# RULES §6: these are persistence-defined by their own windows -> 1 tick.
_FAST_FAULT_SUBTYPES = ("STUCK", "DROPOUT", "SPIKE")
# RULES §4 R1: hard transport flags with HIGH vs MED confidence.
_TRANSPORT_HIGH = ("SIG_INVALID", "SEQ_REPLAY", "STATION_MISMATCH",
                   "UNKNOWN_STATION")

_CONF_ORDER = (Conf.NONE, Conf.LOW, Conf.MED, Conf.HIGH)


@dataclass(frozen=True)
class Candidate:
    """RULES §4 output for one tick, before §6 persistence."""
    label: Label
    subtype: Optional[str] = None
    confidence: Conf = Conf.LOW
    rule_id: str = ""
    reasons: tuple[str, ...] = ()


def _boost(conf: Conf) -> Conf:
    """PA2: raise one level, capped at HIGH."""
    idx = min(_CONF_ORDER.index(conf) + 1, _CONF_ORDER.index(Conf.HIGH))
    return _CONF_ORDER[idx]


def decide(ev: Evidence, b_state: SensorState) -> Candidate:
    """RULES §4, first match wins, then PA1/PA2/PA3. N-10 holds because R2
    keys on shape_ok, which RATE_EXCEEDED never sets."""
    b_hard = tuple(ev.transport_hard.get("B") or ())

    # R1 — hard transport flag on B's channel. Subtype is the specific flag
    # so the tracker's config-driven immediacy check (§6, MEMORY.md §24 Q1)
    # applies: SIG_INVALID/SEQ_REPLAY/STATION_MISMATCH/UNKNOWN_STATION commit
    # at 1 tick, TS_SKEW/TS_NONMONOTONIC stay on the 3-tick rule.
    if b_hard:
        subtype = next((f for f in b_hard if f in _TRANSPORT_HIGH), b_hard[0])
        conf = Conf.HIGH if subtype in _TRANSPORT_HIGH else Conf.MED
        cand = Candidate(_ATTACK, subtype, conf, "R1",
                         tuple(f"TRANSPORT_{f}" for f in b_hard))

    # R2 — shape plausibility failed.
    elif ev.shape_ok is False:
        b_flags = tuple(ev.health_flags.get("B") or ())
        subtype = next((f for f in _FAULT_PRIORITY if f in b_flags), "SHAPE")
        cand = Candidate(_FAULT, subtype, Conf.HIGH, "R2",
                         tuple(f"SHAPE_{f}" for f in b_flags))

    # R2b — verbatim replay that physics cannot explain.
    elif ev.replay_match and ev.context != Context.CONSISTENT:
        cand = Candidate(_ATTACK, "REPLAY", Conf.HIGH, "R2b",
                         (f"CONTEXT_{ev.context.value}", "REPLAY_MATCH"))

    # R3 — not enough trustworthy inputs.
    elif ev.context == Context.INSUFFICIENT:
        if ev.notable:
            cand = Candidate(Label.UNCERTAIN, None, Conf.LOW, "R3",
                             ("INSUFFICIENT_INPUTS",))
        else:
            reasons = ("INSUFFICIENT_INPUTS", "DEGRADED_INPUTS") if ev.degraded \
                else ("INSUFFICIENT_INPUTS",)
            cand = Candidate(Label.NORMAL, None, Conf.LOW, "R3", reasons)

    else:
        up_code = f"UPSTREAM_{ev.up_trend.value}"
        rain_code = f"RAIN_{ev.rain.value}"
        down_code = f"DOWNSTREAM_{ev.downstream.value}"

        # R4 — reading matches physics.
        if ev.context == Context.CONSISTENT:
            if not ev.notable:
                cand = Candidate(Label.NORMAL, None, Conf.LOW, "R4",
                                 ("CONTEXT_CONSISTENT",))
            elif ev.rain == Support.NO and ev.downstream != Downstream.YES:
                cand = Candidate(Label.UNCERTAIN, None, Conf.LOW, "R4",
                                 ("CONTEXT_CONSISTENT", "NO_RAIN_SUPPORT"))
            else:
                cand = Candidate(Label.REAL_FLOOD, None, Conf.LOW, "R4",
                                 ("CONTEXT_CONSISTENT", rain_code, down_code))

        # R5 — reading far above what physics explains.
        elif ev.context == Context.PHANTOM:
            if ev.drift:
                cand = Candidate(_FAULT, "DRIFT", Conf.MED, "R5",
                                 ("CONTEXT_PHANTOM", "DRIFT_PATTERN"))
            elif ev.up_trend in (Trend.FLAT, Trend.FALLING) \
                    and ev.rain != Support.YES:
                cand = Candidate(_ATTACK, "FABRICATED", Conf.MED, "R5",
                                 ("CONTEXT_PHANTOM", up_code, rain_code))
            else:
                cand = Candidate(Label.UNCERTAIN, None, Conf.MED, "R5",
                                 ("CONTEXT_PHANTOM", up_code, rain_code,
                                  "CONFLICTING_EVIDENCE"))

        # R6 — reading far below what physics explains.
        elif ev.context == Context.SUPPRESSED:
            if ev.drift:
                cand = Candidate(_FAULT, "DRIFT", Conf.MED, "R6",
                                 ("CONTEXT_SUPPRESSED", "DRIFT_PATTERN"))
            elif ev.up_trend == Trend.RISING or ev.rain == Support.YES:
                cand = Candidate(_ATTACK, "SUPPRESSION", Conf.MED, "R6",
                                 ("CONTEXT_SUPPRESSED", up_code, rain_code))
            else:
                cand = Candidate(Label.UNCERTAIN, None, Conf.MED, "R6",
                                 ("CONTEXT_SUPPRESSED", up_code, rain_code,
                                  "UNSUPPORTED_DEVIATION"))

        # R7 — somewhat unusual, not clearly wrong.
        else:
            if ev.notable or b_state == SensorState.SUSPECT:
                cand = Candidate(Label.UNCERTAIN, None, Conf.LOW, "R7",
                                 ("AMBIGUOUS_RESIDUAL",))
            else:
                cand = Candidate(Label.NORMAL, None, Conf.LOW, "R7",
                                 ("AMBIGUOUS_RESIDUAL",))

    # PA1 — real/uncertain flood claim that the downstream gauge refutes
    # after the physical lag.
    if cand.label in (Label.REAL_FLOOD, Label.UNCERTAIN) and ev.notable \
            and ev.downstream == Downstream.NO:
        cand = Candidate(_ATTACK, "DOWNSTREAM_MISMATCH", Conf.MED, "PA1",
                         cand.reasons + ("DOWNSTREAM_NO",))

    # PA2 — boosters raise an existing attack candidate one level.
    if cand.label == _ATTACK:
        extra: list[str] = []
        if ev.replay_match and "REPLAY_MATCH" not in cand.reasons:
            extra.append("REPLAY_MATCH")
        if ev.noise_too_clean:
            extra.append("NOISE_TOO_CLEAN")
        if extra:
            cand = Candidate(cand.label, cand.subtype,
                             _boost(cand.confidence), cand.rule_id,
                             cand.reasons + tuple(extra))

    # PA3 — REAL confidence ladder.
    if cand.label == Label.REAL_FLOOD:
        if ev.downstream == Downstream.YES:
            conf = Conf.HIGH
        elif ev.rain == Support.YES:
            conf = Conf.MED
        else:
            conf = Conf.LOW
        cand = Candidate(cand.label, cand.subtype, conf, cand.rule_id,
                         cand.reasons)

    return cand


class VerdictTracker:
    """RULES §6: candidate -> committed verdict with hysteresis.

    Committed REAL is sticky: any non-REAL candidate needs
    `real_downgrade_ticks` consecutive non-REAL candidates to displace it.
    FAULT/ATTACK need `fault_attack_ticks` identical (label, subtype)
    candidates, except immediate subtypes (config) and STUCK/DROPOUT/SPIKE
    (persistence-defined by their own windows). UNCERTAIN commits at 1
    unless it would displace committed REAL. NORMAL commits at 1 when
    already committed NORMAL, else after real_downgrade_ticks.
    """

    def __init__(self, cfg: PersistenceThresholds):
        self.cfg = cfg
        self.label = Label.NORMAL
        self.subtype: Optional[str] = None
        self._prev_label: Optional[Label] = None
        self._prev_subtype: Optional[str] = None
        self._same_streak = 0          # consecutive identical (label, subtype)
        self._normal_streak = 0        # consecutive NORMAL candidates
        self._nonreal_streak = 0       # consecutive non-REAL candidates

    def _needed(self, cand: Candidate) -> tuple[int, int]:
        """Returns (persist_count, persist_needed) for this candidate."""
        if cand.label == Label.REAL_FLOOD:
            return 1, 1
        if self.label == Label.REAL_FLOOD:
            # Sticky REAL: any non-REAL candidate counts toward the downgrade.
            return self._nonreal_streak, self.cfg.real_downgrade_ticks
        if cand.label == Label.UNCERTAIN:
            return 1, 1
        if cand.label == Label.NORMAL:
            if self.label == Label.NORMAL:
                return 1, 1
            return self._normal_streak, self.cfg.real_downgrade_ticks
        # FAULT / ATTACK
        immediate = cand.subtype in self.cfg.immediate_subtypes or \
            cand.subtype in _FAST_FAULT_SUBTYPES
        if immediate:
            return 1, 1
        return self._same_streak, self.cfg.fault_attack_ticks

    def update(self, ts: datetime, tick_idx: int, cand: Candidate) -> Verdict:
        same = cand.label == self._prev_label \
            and cand.subtype == self._prev_subtype
        self._prev_label, self._prev_subtype = cand.label, cand.subtype
        self._same_streak = self._same_streak + 1 if same else 1
        self._normal_streak = self._normal_streak + 1 \
            if cand.label == Label.NORMAL else 0
        self._nonreal_streak = self._nonreal_streak + 1 \
            if cand.label != Label.REAL_FLOOD else 0

        count, needed = self._needed(cand)
        if count >= needed:
            new_label, new_subtype = cand.label, cand.subtype
        else:
            new_label, new_subtype = self.label, self.subtype
        changed = new_label != self.label or new_subtype != self.subtype
        self.label, self.subtype = new_label, new_subtype

        return Verdict(
            ts=ts,
            tick_idx=tick_idx,
            candidate=cand.label,
            candidate_subtype=cand.subtype,
            label=self.label,
            subtype=self.subtype,
            confidence=cand.confidence,
            persist_count=count,
            persist_needed=needed,
            rule_id=cand.rule_id,
            reasons=cand.reasons,
            changed=changed,
        )
