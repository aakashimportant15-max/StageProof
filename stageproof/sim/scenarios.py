"""Scenario manifests and the signed (TickInput, TruthTick) stream (T036/T037).

The stream replays REAL held-out data (data/reach.csv via data.slice_window)
with a sensor layer on top: seeded noise + quantization, corruptions from the
manifest, per-station HMAC signing, then post-signing transport directives.
It yields (TickInput, TruthTick) pairs; ONLY TickInput may reach Runner.step —
TruthTick stays with the driver (ARCHITECTURE §9.1). This module never
imports engine modules (models/checks/evidence/decision/pipeline/...).

evaluate_expectations() machine-checks the manifest's `expected` block
(RULES §19) against the committed TickRecord history (T037).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..data import slice_window
from ..domain import (Scenario, SensorReading, TickInput, TruthTick,
                      dataclass_from_dict, dataclass_to_dict)
from ..security.transport import sign_reading
from . import corrupt

__all__ = ["ScenarioError", "load_manifest", "save_manifest",
           "ScenarioStream", "ExpectationResult", "evaluate_expectations",
           "ROLES"]

ROLES = corrupt.ROLES


class ScenarioError(Exception):
    """Scenario manifest or stream construction problem."""


# ---------------------------------------------------------------------------
# manifest IO (T036)
# ---------------------------------------------------------------------------

def load_manifest(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"cannot read scenario manifest {path}: {exc}")
    scenario = dataclass_from_dict(raw, Scenario)
    _validate(scenario, path)
    return scenario


def save_manifest(scenario: Scenario, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dataclass_to_dict(scenario), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _validate(scenario: Scenario, source) -> None:
    if not scenario.id:
        raise ScenarioError(f"{source}: manifest has no id")
    window = scenario.window or {}
    if not window.get("start") or not window.get("end"):
        raise ScenarioError(
            f"{source}: scenario '{scenario.id}' needs window.start/end")
    try:
        datetime.fromisoformat(str(window["start"]))
        datetime.fromisoformat(str(window["end"]))
    except ValueError as exc:
        raise ScenarioError(f"{source}: bad window timestamps: {exc}")
    for entry in scenario.corruptions:
        ctype = (entry or {}).get("type", "")
        role = (entry or {}).get("role", "")
        if ctype not in corrupt.FAULT_TYPES and ctype not in corrupt.ATTACK_TYPES:
            raise ScenarioError(
                f"{source}: corruption type '{ctype}' is not a known "
                f"fault/attack family")
        if role not in ROLES:
            raise ScenarioError(f"{source}: corruption role '{role}' invalid")
    for atk in scenario.transport_attacks:
        atype = (atk or {}).get("type", "")
        if atype not in corrupt.DIRECTIVE_TYPES:
            raise ScenarioError(
                f"{source}: transport attack type '{atype}' invalid")
    for out in scenario.feed_outages:
        feed = (out or {}).get("feed", "")
        if feed not in ROLES + ("RAIN",):
            raise ScenarioError(f"{source}: outage feed '{feed}' invalid")


# ---------------------------------------------------------------------------
# the stream (T036)
# ---------------------------------------------------------------------------

def _f(v) -> float | None:
    v = float(v)
    return v if np.isfinite(v) else None


def _active(entries, tick: int) -> list[dict]:
    return [e for e in entries
            if int(e.get("start_tick", 0)) <= tick <= int(e.get("end_tick", -1))]


class ScenarioStream:
    """Deterministic (TickInput, TruthTick) stream over a manifest.

    Same seed => identical packets, signatures and truth (test AC-DET for the
    sim side). The RNG is consumed in a fixed order (noise per role in
    A,T,B,C order, then noise-burst draws in manifest order) BEFORE the first
    tick is produced, so iteration order and outages cannot shift draws.
    """

    def __init__(self, scenario: Scenario, df: pd.DataFrame,
                 stations: dict, keys: dict, tick_minutes: int = 15,
                 stage_unit: str = "ft"):
        _validate(scenario, f"scenario {scenario.id}")
        self.scenario = scenario
        self.tick_minutes = int(tick_minutes)
        self.stage_unit = stage_unit
        self.keys = dict(keys)

        self.df = slice_window(df, scenario.window["start"],
                               scenario.window["end"],
                               warmup_ticks=scenario.warmup_ticks)
        self.n_ticks = len(self.df)

        missing = [r for r in ROLES if r not in stations]
        if missing:
            raise ScenarioError(
                f"station params missing for roles: {', '.join(missing)}")
        self.stations = stations
        for role in ROLES:
            if role not in self.keys:
                raise ScenarioError(f"no signing key for role '{role}'")

        self._corrupted_roles = {c.get("role") for c in scenario.corruptions}
        self._compromised = set(scenario.compromised_keys)

        # -- sensor layer: seeded noise + quantization, then corruptions ----
        rng = np.random.default_rng(scenario.seed)
        observed: dict[str, np.ndarray] = {}
        for role in ROLES:
            true = self.df[f"{role}_stage"].to_numpy(dtype=float)
            st = stations[role]
            observed[role] = corrupt.emulate_sensor(
                true, rng, float(st.get("noise_std", 0.0)),
                float(st.get("quant_step", 0.01)))
        for entry in scenario.corruptions:
            role = entry["role"]
            ctype = entry["type"]
            start = int(entry.get("start_tick", 0))
            end = int(entry.get("end_tick", self.n_ticks - 1))
            params = entry.get("params") or {}
            observed[role] = self._apply_corruption(
                observed[role], ctype, start, end, params, rng,
                stations[role])
        self._observed = observed

        # -- per-channel packet state --------------------------------------
        self._seq = {role: 0 for role in ROLES}
        self._history: dict[str, list] = {role: [] for role in ROLES}
        self._directives: dict[int, list[dict]] = {}
        for atk in scenario.transport_attacks:
            self._directives.setdefault(int(atk["tick"]), []).append(atk)

    # -- corruption dispatch -------------------------------------------------

    def _apply_corruption(self, series: np.ndarray, ctype: str, start: int,
                          end: int, params: dict, rng: np.random.Generator,
                          station: dict) -> np.ndarray:
        n = len(series)
        end = min(end, n - 1)
        start = max(start, 0)
        if ctype == "DROPOUT":
            return corrupt.apply_dropout(series, start, end)
        if ctype == "STUCK":
            return corrupt.apply_stuck(series, start, end,
                                       params.get("level"))
        if ctype == "SPIKE":
            return corrupt.apply_spike(series, int(params.get("tick", start)),
                                       float(params.get("magnitude", 1.0)))
        if ctype == "NOISE_BURST":
            # Size the burst against the engine's fitted NOISE baseline when
            # present (run_scenario.py injects it); fall back to the quiet-week
            # config value so unit fixtures stay deterministic.
            return corrupt.apply_noise_burst(
                series, start, end, rng, float(params.get("ratio", 10.0)),
                float(station.get("noise_baseline_std",
                                  station.get("noise_std", 0.01))))
        if ctype == "DRIFT":
            return corrupt.apply_drift(series, start, end,
                                       float(params.get("total_offset", 0.3)))
        if ctype == "RANGE_OOB":
            return corrupt.apply_range_oob(
                series, start, end, float(station.get("sensor_min", 0.0)),
                float(station.get("sensor_max", 30.0)),
                float(params.get("margin", 0.5)),
                high=bool(params.get("high", True)))
        if ctype == "FABRICATED_RAMP":
            return corrupt.apply_fabricated_ramp(
                series, start, float(params["peak_stage"]),
                int(params.get("rise_ticks",
                               int(params.get("rise_hours", 6))
                               * 60 // self.tick_minutes)),
                int(params.get("hold_ticks",
                               int(params.get("hold_hours", 2))
                               * 60 // self.tick_minutes)),
                int(params.get("decline_ticks",
                               int(params.get("decline_hours", 8))
                               * 60 // self.tick_minutes)),
                params.get("max_per_tick"))
        if ctype == "SUPPRESSION":
            baseline = params.get("baseline")
            if baseline is None:
                pre = series[max(start - 4, 0):start]
                baseline = float(np.nanmean(pre)) if len(pre) else 0.0
            return corrupt.apply_suppression(
                series, start, end, float(baseline),
                float(params.get("strength", 0.85)))
        raise ScenarioError(f"unknown corruption type '{ctype}'")

    # -- helpers ---------------------------------------------------------------

    def _outaged(self, feed: str, tick: int) -> bool:
        return bool(_active(self.scenario.feed_outages, tick)
                    and any(o.get("feed") == feed
                            for o in _active(self.scenario.feed_outages, tick)))

    def corrupted_roles_at(self, tick: int) -> tuple[str, ...]:
        roles = {e.get("role") for e in _active(self.scenario.corruptions, tick)}
        for atk in self._directives.get(tick, []):
            roles.add(atk.get("role"))
        return tuple(r for r in ROLES if r in roles)

    def volunteer_replies_at(self, tick: int) -> list[dict]:
        est = self.scenario.event_start_tick
        return [dict(v) for v in self.scenario.volunteer_script
                if est + int(v.get("tick_offset", 0)) == tick]

    def operator_events_at(self, tick: int) -> list[dict]:
        est = self.scenario.event_start_tick
        return [dict(o) for o in self.scenario.operator_script
                if est + int(o.get("tick_offset", 0)) == tick]

    # -- stream ----------------------------------------------------------------

    def __len__(self) -> int:
        return self.n_ticks

    def tick(self, idx: int) -> tuple[TickInput, TruthTick]:
        if not 0 <= idx < self.n_ticks:
            raise ScenarioError(
                f"tick {idx} outside stream 0..{self.n_ticks - 1}")
        ts = self.df.index[idx].to_pydatetime()
        row = self.df.iloc[idx]

        readings: dict[str, SensorReading | None] = {}
        for role in ROLES:
            if self._outaged(role, idx):
                readings[role] = None
                continue
            stage = self._observed[role][idx]
            if not np.isfinite(stage):
                readings[role] = None
                continue
            self._seq[role] += 1
            sig = sign_reading(role, ts, self._seq[role], float(stage),
                               self.stage_unit, self.keys[role])
            reading = SensorReading(station_id=role, ts=ts,
                                    seq=self._seq[role], stage=float(stage),
                                    unit=self.stage_unit, sig=sig)
            self._history[role].append(reading)
            readings[role] = reading

        # transport directives AFTER signing (RULES §19 F)
        for atk in self._directives.get(idx, []):
            role = atk.get("role")
            current = readings.get(role)
            if atk["type"] == "UNSIGNED_INJECT":
                if current is not None:
                    readings[role] = corrupt.unsigned_inject(
                        current, float(atk.get("params", {}).get("stage",
                                                                 current.stage)))
            elif atk["type"] == "REPLAY_PACKET":
                replayed = corrupt.replay_packet(
                    self._history[role][:-1],
                    int(atk.get("params", {}).get("from_offset_ticks", 4)))
                if replayed is not None:
                    readings[role] = replayed

        rain_feed_ok = not self._outaged("RAIN", idx)
        rain = _f(row["rain_mm_prev_hr"]) if rain_feed_ok else None

        tick_input = TickInput(ts=ts, tick_idx=idx, readings=readings,
                               rain_prev_hr_mm=rain, rain_feed_ok=rain_feed_ok)

        est = self.scenario.event_start_tick
        gt = self.scenario.ground_truth or {}
        truth = TruthTick(
            ts=ts,
            true_q_B=_f(row["B_q"]),
            truth_label=str(gt.get("label", "NORMAL")) if idx >= est else "NORMAL",
            true_stage={r: _f(row[f"{r}_stage"]) for r in ROLES},
            corrupted_roles=self.corrupted_roles_at(idx),
            truth_notable=bool(gt.get("truth_notable", False)) if idx >= est else False,
        )
        return tick_input, truth

    def __iter__(self):
        for idx in range(self.n_ticks):
            yield self.tick(idx)


# ---------------------------------------------------------------------------
# expectation evaluator (T037; RULES §19)
# ---------------------------------------------------------------------------

_ALERT_ORDER = {"NONE": 0, "WATCH": 1, "PROVISIONAL_WARNING": 2,
                "CONFIRMED_WARNING": 3, "CLEARED": 1}


@dataclass(frozen=True)
class ExpectationResult:
    id: str
    passed: bool
    detail: str


def evaluate_expectations(scenario: Scenario, records) -> list[ExpectationResult]:
    """Machine-check a manifest's `expected` block against TickRecords.

    committed: the label (and subtype if given) must be committed at some
    tick within [from_tick - tol, to_tick + tol]. sensor: the state must be
    reached by by_tick. alert: must_reach (within within_ticks of
    event_start_tick; source checked when given) / must_not_exceed (alert
    never above the named state). actions: type present/absent over the run.
    baselines: rollz_flags_min on the comparison baseline;
    threshold_alarm expected_true/expected_false."""
    exp = scenario.expected or {}
    results: list[ExpectationResult] = []
    n = len(records)

    def labels_at(t: int):
        v = records[t].verdict
        return (v.label.value if v is not None else None,
                v.subtype if v is not None else None)

    for i, c in enumerate(exp.get("committed") or []):
        want = c["label"]
        subtype = c.get("subtype")
        tol = int(c.get("tolerance_ticks", 0))
        lo = max(int(c.get("from_tick", 0)) - tol, 0)
        hi = min(int(c.get("to_tick", n - 1)) + tol, n - 1)
        hit = None
        for t in range(lo, hi + 1):
            label, sub = labels_at(t)
            if label == want and (subtype is None or sub == subtype):
                hit = t
                break
        want_s = f"{want}/{subtype}" if subtype else want
        results.append(ExpectationResult(
            id=f"committed[{i}]:{want_s}", passed=hit is not None,
            detail=(f"committed at tick {hit}" if hit is not None
                    else f"no commit in ticks [{lo}..{hi}]")))

    for i, s in enumerate(exp.get("sensor") or []):
        role, want, by = s["role"], s["state"], int(s.get("by_tick", n - 1))
        hit = None
        for t in range(0, min(by, n - 1) + 1):
            st = records[t].sensor_status.get(role)
            if st is not None and st.state.value == want:
                hit = t
                break
        results.append(ExpectationResult(
            id=f"sensor[{i}]:{role}={want}", passed=hit is not None,
            detail=(f"reached at tick {hit} (by {by})" if hit is not None
                    else f"never reached by tick {by}")))

    alert_exp = exp.get("alert") or {}
    est = scenario.event_start_tick
    if alert_exp.get("must_reach"):
        want = alert_exp["must_reach"]
        within = alert_exp.get("within_ticks")
        hi = min(est + int(within), n - 1) if within is not None else n - 1
        src = alert_exp.get("source")
        hit = None
        for t in range(0, hi + 1):
            if records[t].alert_state.value == want and (
                    src is None or records[t].alert_source == src):
                hit = t
                break
        results.append(ExpectationResult(
            id=f"alert:reaches:{want}" + (f"({src})" if src else ""),
            passed=hit is not None,
            detail=(f"reached at tick {hit}" if hit is not None
                    else f"never reached by tick {hi}")))
    if alert_exp.get("must_not_exceed"):
        cap = _ALERT_ORDER[alert_exp["must_not_exceed"]]
        bad = [t for t in range(n)
               if _ALERT_ORDER.get(records[t].alert_state.value, 99) > cap]
        results.append(ExpectationResult(
            id=f"alert:never_above:{alert_exp['must_not_exceed']}",
            passed=not bad,
            detail=("held" if not bad else f"exceeded at ticks {bad[:5]}")))

    acts = exp.get("actions") or {}
    seen = {a.type.value for r in records for a in r.new_actions}
    for want in acts.get("must_include") or []:
        results.append(ExpectationResult(
            id=f"action:includes:{want}", passed=want in seen,
            detail=("present" if want in seen else "absent from all actions")))
    for want in acts.get("must_not_include") or []:
        results.append(ExpectationResult(
            id=f"action:excludes:{want}", passed=want not in seen,
            detail=("absent" if want not in seen
                    else f"unexpectedly present")))

    base_exp = exp.get("baselines") or {}
    if base_exp.get("rollz_flags_min") is not None:
        flags = sum(1 for r in records
                    if r.baselines and r.baselines.get("rollz_flag"))
        need = int(base_exp["rollz_flags_min"])
        results.append(ExpectationResult(
            id="baselines:rollz_flags_min", passed=flags >= need,
            detail=f"{flags} flagged ticks (need >= {need})"))
    if "threshold_alarm" in base_exp:
        want_true = base_exp["threshold_alarm"] == "expected_true"
        alarms = sum(1 for r in records
                     if r.baselines and r.baselines.get("threshold_alert"))
        results.append(ExpectationResult(
            id=f"baselines:threshold_alarm:{base_exp['threshold_alarm']}",
            passed=(alarms > 0) if want_true else (alarms == 0),
            detail=f"{alarms} alarmed ticks"))
    return results
