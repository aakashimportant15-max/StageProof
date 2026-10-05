"""Build scenario manifests A-F from real held-out data (TASKS T038).

Per ARCHITECTURE.md section 9.3: each manifest anchors on a REAL event window
from data/reach.csv, with the corruption layer placed at ticks computed from
the data itself. Every builder assertion (T038) is checked here and fails
LOUDLY — if an assertion cannot be satisfied the correct response is to
escalate to the human, never to weaken RULES.

Windows: window.start anchors event_start_tick (= warmup_ticks), because
data.slice_window prepends exactly warmup_ticks rows before window.start.
All stream tick indices used in `expected` therefore equal
warmup_ticks + (offset from window.start).

Usage:  python scripts/build_scenarios.py [--out data/scenarios]
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stageproof.data import load_model_artifact  # noqa: E402
from stageproof.domain import Scenario  # noqa: E402
from stageproof.sim.scenarios import save_manifest  # noqa: E402

TICK = timedelta(minutes=15)


def fail(msg: str) -> None:
    raise AssertionError(f"build_scenarios: {msg}")


def need(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)


# ---------------------------------------------------------------------------
# shared inputs
# ---------------------------------------------------------------------------

def load_inputs():
    with open(ROOT / "config" / "reach.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    art = load_model_artifact(ROOT / "artifacts" / "model.json")
    df = pd.read_csv(ROOT / "data" / "reach.csv", index_col="ts_utc",
                     parse_dates=True)
    return cfg, art, df


def first_cross(w: pd.DataFrame, col: str, level: float) -> int:
    hit = w.index[w[col] >= level]
    if len(hit) == 0:
        fail(f"{col} never reaches {level} in window "
             f"[{w.index[0]} .. {w.index[-1]}]")
    return int(w.index.get_loc(hit[0]))


# ---------------------------------------------------------------------------
# A — real flood passes through untampered (RULES 19 A)
# ---------------------------------------------------------------------------

def build_a(cfg, art, df, warmup: int) -> Scenario:
    ev = cfg["events"]["main_flood"]
    watch = float(cfg["levels"]["B"]["watch_stage"])
    action = float(cfg["levels"]["B"]["action_stage"])
    w = df.loc[ev["start"]:ev["end"]]
    t_watch = first_cross(w, "B_stage", watch)
    t_act = first_cross(w, "B_stage", action)
    lag = int(art["lag_BC_ticks"])
    need(t_act + lag + 12 < len(w) - 1,
         "A: window too short for downstream confirmation after the "
         "action-stage crossing; widen main_flood window")
    est = warmup
    s_watch, s_act = est + t_watch, est + t_act
    return Scenario(
        id="A",
        title="Real flood, clean feed (2020-11 James River)",
        description=("Untampered main_flood event: B crosses watch then "
                     "action stage; engine must commit REAL_FLOOD and walk "
                     "the alert ladder on observed data alone."),
        seed=101,
        window={"start": str(w.index[0].isoformat()),
                "end": str(w.index[-1].isoformat())},
        warmup_ticks=warmup,
        event_start_tick=est,
        ground_truth={"label": "REAL_FLOOD", "subtype": None,
                      "truth_notable": True},
        expected={
            "committed": [
                {"label": "REAL_FLOOD", "subtype": None,
                 "from_tick": s_watch - 2, "to_tick": s_watch + 6,
                 "tolerance_ticks": 2},
            ],
            "alert": {
                "must_reach": "PROVISIONAL_WARNING", "source": "OBSERVED",
                "within_ticks": (s_act - est) + 24,
            },
            "actions": {
                "must_include": ["SEND_PUBLIC_WARNING"],
                "must_not_include": ["QUARANTINE_SENSOR",
                                     "RAISE_SECURITY_ALERT",
                                     "OPEN_MAINTENANCE_TICKET"],
            },
            "baselines": {"rollz_flags_min": 1,
                          "threshold_alarm": "expected_true"},
        },
    )


# ---------------------------------------------------------------------------
# B — genuine STUCK fault on the rising limb (RULES 19 B)
# ---------------------------------------------------------------------------

def build_b(cfg, art, df, warmup: int) -> Scenario:
    ev = cfg["events"]["moderate_event"]
    watch = float(cfg["levels"]["B"]["watch_stage"])
    action = float(cfg["levels"]["B"]["action_stage"])
    stuck_ticks = 8          # config/thresholds.yaml health.stuck_ticks
    stuck_len = 16
    w = df.loc[ev["start"]:ev["end"]]
    t_act = first_cross(w, "B_stage", action)
    t0 = t_act - 30                       # stuck begins 30 ticks below action
    need(t0 - stuck_ticks - 2 >= 0,
         "B: stuck window would begin before the event window")

    stage = w["B_stage"].to_numpy(dtype=float)
    q = w["B_q"].to_numpy(dtype=float)
    # T038: B's true flow change must satisfy the STUCK condition
    # (|pred change| over stuck_ticks >= stuck_pred_change_min + margin).
    i = t0 + stuck_ticks
    dlnq = math.log(q[i]) - math.log(q[i - stuck_ticks])
    need(dlnq >= 0.07,
         f"B: true ln Q change over {stuck_ticks} ticks is {dlnq:.3f}, "
         f"below 0.07 — the STUCK detector would not fire")
    need(float(stage[t0:t_act].max()) < action,
         "B: true stage reaches action_stage inside the stuck stretch")
    need(float(stage[t0]) < watch,
         "B: stuck level would freeze at/above watch stage")

    start = w.index[t0 - 8]
    end = w.index[t_act + 2]
    est = warmup
    s0 = est + 8                          # corruption start inside window
    return Scenario(
        id="B",
        title="Stuck sensor while the river rises (2024-01 event)",
        description=("B freezes for 16 ticks on the rising limb of a real "
                     "flood, below action stage. Engine must commit "
                     "SENSOR_FAULT/STUCK, quarantine B, open a maintenance "
                     "ticket, and never warn the public."),
        seed=202,
        window={"start": str(start.isoformat()), "end": str(end.isoformat())},
        warmup_ticks=warmup,
        event_start_tick=est,
        corruptions=({
            "role": "B", "type": "STUCK", "start_tick": s0,
            "end_tick": s0 + stuck_len - 1, "params": {},
        },),
        ground_truth={"label": "REAL_FLOOD", "subtype": "SENSOR_FAULT/STUCK",
                      "truth_notable": True},
        expected={
            "committed": [
                {"label": "SENSOR_FAULT", "subtype": "STUCK",
                 "from_tick": s0 + 4, "to_tick": s0 + 12,
                 "tolerance_ticks": 2},
            ],
            "sensor": [{"role": "B", "state": "QUARANTINED",
                        "by_tick": s0 + stuck_len + 8}],
            "alert": {"must_not_exceed": "WATCH"},
            "actions": {
                "must_include": ["OPEN_MAINTENANCE_TICKET"],
                "must_not_include": ["SEND_PUBLIC_WARNING",
                                     "RAISE_SECURITY_ALERT"],
            },
            "baselines": {"threshold_alarm": "expected_false"},
        },
    )


# ---------------------------------------------------------------------------
# C — fabricated ramp with a compromised key in a dry period (RULES 19 C)
# ---------------------------------------------------------------------------

def _rain_acc48(hourly: pd.Series, yes_mm: float) -> pd.Series:
    """Replicates checks.rain_support: trailing 48-h accumulation."""
    acc = hourly.rolling(48).mean() * 48.0
    return acc


def build_c(cfg, art, df, warmup: int) -> Scenario:
    """C is placed on the driest rain-free stretch of the full record (the
    named `dry_window` event has rain patches that keep the 48-h trailing
    accumulation near YES for most of its length), scanning for a placement
    that is also flat-upstream and dry-B across the ramp."""
    watch = float(cfg["levels"]["B"]["watch_stage"])
    action = float(cfg["levels"]["B"]["action_stage"])
    yes_mm = float(art["rain"]["yes_mm"])
    peak = 18.0
    need(peak >= action + 0.5,
         "C: peak_stage must be at least action_stage + 0.5 ft")
    ramp_ticks = (6 + 2 + 8) * 4          # rise 6h + hold 2h + decline 8h

    need(df.index[0].minute == 0,
         "C: dataset start is not aligned to the hour; the hourly rain "
         "series used to mirror checks.rain_support would be off-grid")
    hser = df.loc[df.index.minute == 0, "rain_mm_prev_hr"]
    hour_rows = np.flatnonzero(np.asarray(df.index.minute == 0))
    s = pd.Series(hser.to_numpy(dtype=float),
                  index=pd.RangeIndex(len(hser)))
    acc = _rain_acc48(s, yes_mm)
    ok = acc < (yes_mm - 0.05)
    # longest run of consecutive hours whose trailing 48-h rain is sub-YES
    runs: list[tuple[int, int]] = []
    i0 = None
    for i, flag in enumerate(ok.to_numpy()):
        if flag and i0 is None:
            i0 = i
        elif not flag and i0 is not None:
            runs.append((i0, i - 1))
            i0 = None
    if i0 is not None:
        runs.append((i0, len(ok) - 1))
    need(bool(runs), "C: no sub-YES rain run inside the dataset at all")
    rs, re = max(runs, key=lambda r: r[1] - r[0])
    run_h = re - rs + 1
    # ramp needs [rise-48h-2h, rise+ramp+2h] inside the run
    need(run_h >= 48 + ramp_ticks // 4 + 6,
         f"C: longest no-rain run is {run_h} h (need >= "
         f"{48 + ramp_ticks // 4 + 6} h of 48-h accumulation below "
         f"{yes_mm:.3f} mm) — the dataset has no clean stretch for the ramp; "
         f"escalate: pick a different dry event, do NOT weaken RULES")

    # pick the first placement inside the clean run that is flat-upstream
    # (max 8-tick dlnQ on A <= 0.08) and dry on B (< watch) across the ramp
    a_q = df["A_q"].to_numpy(dtype=float)
    b_stage = df["B_stage"].to_numpy(dtype=float)
    chosen: Optional[int] = None
    for cand in range(rs + 50, re - ramp_ticks // 4 - 1):
        r0 = int(hour_rows[cand])
        if r0 < warmup + 2 or r0 + 101 > len(df):
            continue                      # need warmup history + window tail
        ai0 = r0 - 2
        ai1 = r0 + ramp_ticks + 2
        if ai1 + 8 >= len(df):
            continue
        dln = np.log(a_q[ai0 + 8:ai1 + 1]) - np.log(a_q[ai0:ai1 + 1 - 8])
        if float(np.nanmax(dln)) > 0.08:
            continue
        if float(np.nanmax(b_stage[ai0:ai1 + 1])) >= watch:
            continue
        chosen = cand
        break
    need(chosen is not None,
         "C: no placement inside the clean run satisfies flat-upstream + "
         "dry-B across the ramp — escalate: pick a different stretch, do "
         "NOT weaken RULES")
    rise = chosen
    r0 = int(hour_rows[rise])
    w = df.iloc[r0:r0 + 101]
    need(len(w) == 101,
         "C: window tail runs past the end of the dataset")
    start, end = w.index[0], w.index[-1]
    est = warmup
    return Scenario(
        id="C",
        title="Fabricated flood from a compromised key, dry period",
        description=("Attacker holding B's key fabricates a flood ramp to "
                     f"{peak} ft during a rain-free, flat-upstream stretch. "
                     "All signatures verify; physics must not. Engine must "
                     "commit ATTACK/FABRICATED, quarantine, preserve "
                     "evidence, and never warn the public."),
        seed=303,
        window={"start": str(start.isoformat()), "end": str(end.isoformat())},
        warmup_ticks=warmup,
        event_start_tick=est,
        compromised_keys=("B",),
        corruptions=({
            "role": "B", "type": "FABRICATED_RAMP", "start_tick": est,
            "end_tick": est + ramp_ticks - 1,
            "params": {"peak_stage": peak, "rise_hours": 6, "hold_hours": 2,
                       "decline_hours": 8},
        },),
        ground_truth={"label": "NORMAL", "subtype": None,
                      "truth_notable": False},
        expected={
            "committed": [
                {"label": "POSSIBLE_CYBER_ATTACK", "subtype": "FABRICATED",
                 "from_tick": est + 18, "to_tick": est + 63 + 20,
                 "tolerance_ticks": 2},
            ],
            "sensor": [{"role": "B", "state": "QUARANTINED",
                        "by_tick": est + ramp_ticks + 12}],
            "alert": {"must_not_exceed": "WATCH"},
            "actions": {
                "must_include": ["RAISE_SECURITY_ALERT", "PRESERVE_EVIDENCE",
                                 "QUARANTINE_SENSOR"],
                "must_not_include": ["SEND_PUBLIC_WARNING"],
            },
            "baselines": {"threshold_alarm": "expected_true"},
        },
    )


# ---------------------------------------------------------------------------
# D — real flood suppressed at B (RULES 19 D)
# ---------------------------------------------------------------------------

def build_d(cfg, art, df, warmup: int) -> Scenario:
    ev = cfg["events"]["main_flood"]
    watch = float(cfg["levels"]["B"]["watch_stage"])
    action = float(cfg["levels"]["B"]["action_stage"])
    strength = 0.85
    w = df.loc[ev["start"]:ev["end"]]
    t_watch = first_cross(w, "B_stage", watch)
    t_act = first_cross(w, "B_stage", action)
    stage = w["B_stage"].to_numpy(dtype=float)

    # suppression starts 4 ticks before the watch crossing; observed is
    # blended toward the pre-start baseline from there on.
    base = float(np.mean(stage[t_watch - 8:t_watch - 4]))
    modeled = base + (1.0 - strength) * (stage[t_watch - 4:t_act + 41] - base)
    need(float(modeled.max()) < action - 0.2,
         f"D: suppressed B would reach {float(modeled.max()):.2f} ft, at/ "
         f"above action {action} — the threshold baseline must stay silent")
    need(stage[t_act] >= action, "D: true stage must cross action in window")

    start = w.index[t_watch - 4]
    end = w.index[t_act + 40]
    est = warmup
    return Scenario(
        id="D",
        title="Real flood suppressed at the target gauge",
        description=("Attacker squashes B's stage toward the pre-event "
                     "baseline while the real river crosses action stage. "
                     "Observed stays below action; upstream/downstream "
                     "physics does not. Engine must commit "
                     "ATTACK/SUPPRESSION and raise a PROVISIONAL estimate-"
                     "based warning."),
        seed=404,
        window={"start": str(start.isoformat()), "end": str(end.isoformat())},
        warmup_ticks=warmup,
        event_start_tick=est,
        corruptions=({
            "role": "B", "type": "SUPPRESSION", "start_tick": est,
            "end_tick": est + 52, "params": {"strength": strength},
        },),
        ground_truth={"label": "REAL_FLOOD", "subtype": None,
                      "truth_notable": True},
        expected={
            "committed": [
                {"label": "POSSIBLE_CYBER_ATTACK", "subtype": "SUPPRESSION",
                 "from_tick": est + 9, "to_tick": est + 30,
                 "tolerance_ticks": 2},
            ],
            "sensor": [{"role": "B", "state": "QUARANTINED",
                        "by_tick": est + 40}],
            "alert": {
                "must_reach": "PROVISIONAL_WARNING", "source": "ESTIMATE",
                "within_ticks": (t_act - (t_watch - 4)) + 30,
            },
            "actions": {"must_include": ["QUARANTINE_SENSOR"],
                        "must_not_include": []},
            "baselines": {"rollz_flags_min": 1,
                          "threshold_alarm": "expected_false"},
        },
    )


# ---------------------------------------------------------------------------
# E — upstream/tributary outage during the rise, volunteers confirm (19 E)
# ---------------------------------------------------------------------------

def build_e(cfg, art, df, warmup: int) -> Scenario:
    ev = cfg["events"]["main_flood"]
    action = float(cfg["levels"]["B"]["action_stage"])
    w = df.loc[ev["start"]:ev["end"]]
    t_watch = first_cross(w, "B_stage", float(
        cfg["levels"]["B"]["watch_stage"]))
    t_act = first_cross(w, "B_stage", action)
    lag = int(art["lag_BC_ticks"])

    start = w.index[t_watch - 2]
    end = w.index[min(t_act + lag + 40, len(w) - 1)]
    est = warmup
    # outage covers A and T from 2 ticks before the watch crossing for 6 ticks
    out = {"start_tick": est, "end_tick": est + 5}
    stage = w["B_stage"].to_numpy(dtype=float)
    need(float(stage[t_watch - 2:t_watch + 5].max()) < action,
         "E: true B would cross action stage during the outage + 3 ticks — "
         "the community path could be preempted by the observed rule")

    return Scenario(
        id="E",
        title="Feed outage during the rise, volunteer verification",
        description=("A and T go dark for 6 ticks as B crosses watch stage. "
                     "Evidence goes INSUFFICIENT; volunteers V1 and V2 "
                     "confirm flooding; the engine must walk UNCERTAIN -> "
                     "PROVISIONAL (COMMUNITY) -> REAL after A returns -> "
                     "CONFIRMED on downstream response."),
        seed=505,
        window={"start": str(start.isoformat()), "end": str(end.isoformat())},
        warmup_ticks=warmup,
        event_start_tick=est,
        feed_outages=({"feed": "A", **out}, {"feed": "T", **out}),
        volunteer_script=(
            {"tick_offset": 4, "volunteer_id": "V1", "code": 1},
            {"tick_offset": 4, "volunteer_id": "V2", "code": 1},
        ),
        ground_truth={"label": "REAL_FLOOD", "subtype": None,
                      "truth_notable": True},
        expected={
            "committed": [
                {"label": "UNCERTAIN", "subtype": None,
                 "from_tick": est + 2, "to_tick": est + 6,
                 "tolerance_ticks": 1},
                {"label": "REAL_FLOOD", "subtype": None,
                 "from_tick": est + 6, "to_tick": est + 16,
                 "tolerance_ticks": 2},
            ],
            "alert": {
                "must_reach": "PROVISIONAL_WARNING", "source": "COMMUNITY",
                "within_ticks": 2 + 12,
            },
            "actions": {
                "must_include": ["REQUEST_VERIFICATION", "SEND_PUBLIC_WARNING"],
                "must_not_include": ["QUARANTINE_SENSOR",
                                     "RAISE_SECURITY_ALERT"],
            },
            "baselines": {"rollz_flags_min": 1},
        },
    )


# ---------------------------------------------------------------------------
# F — unsigned injection then replay on B (RULES 19 F)
# ---------------------------------------------------------------------------

def build_f(cfg, art, df, warmup: int) -> Scenario:
    ev = cfg["events"]["dry_window"]
    action = float(cfg["levels"]["B"]["action_stage"])
    w = df.loc[ev["start"]:ev["end"]]
    inject_at = 16
    replay_at = 24
    total = 56
    stage = w["B_stage"].to_numpy(dtype=float)
    need(float(stage[:total].max()) < action,
         "F: dry-window truth crosses action stage — pick another stretch")
    start = w.index[0]
    end = start + TICK * total
    est = warmup
    need(inject_at >= 8, "F: inject must clear warmup with a clean margin")
    need(replay_at - inject_at >= 4,
         "F: replay needs a clean margin after the injection")
    return Scenario(
        id="F",
        title="Unsigned injection and packet replay on B",
        description=("An attacker without keys injects an unsigned 18 ft "
                     "reading, then replays an old signed packet. Transport "
                     "must reject both (SIG_INVALID, SEQ_REPLAY), the engine "
                     "must commit ATTACK at one tick each, quarantine B, "
                     "raise a security alert, and fall back to estimates."),
        seed=606,
        window={"start": str(start.isoformat()), "end": str(end.isoformat())},
        warmup_ticks=warmup,
        event_start_tick=est,
        transport_attacks=(
            {"role": "B", "type": "UNSIGNED_INJECT", "tick": est + inject_at,
             "params": {"stage": 18.0}},
            {"role": "B", "type": "REPLAY_PACKET", "tick": est + replay_at,
             "params": {"from_offset_ticks": 4}},
        ),
        ground_truth={"label": "NORMAL", "subtype": None,
                      "truth_notable": False},
        expected={
            "committed": [
                {"label": "POSSIBLE_CYBER_ATTACK", "subtype": "SIG_INVALID",
                 "from_tick": est + inject_at - 1,
                 "to_tick": est + inject_at + 2, "tolerance_ticks": 0},
                {"label": "POSSIBLE_CYBER_ATTACK", "subtype": None,
                 "from_tick": est + replay_at - 1,
                 "to_tick": est + replay_at + 2, "tolerance_ticks": 0},
            ],
            "sensor": [{"role": "B", "state": "QUARANTINED",
                        "by_tick": est + replay_at + 4}],
            "alert": {"must_not_exceed": "WATCH"},
            "actions": {
                "must_include": ["QUARANTINE_SENSOR", "RAISE_SECURITY_ALERT",
                                 "PRESERVE_EVIDENCE"],
                "must_not_include": ["SEND_PUBLIC_WARNING"],
            },
        },
    )


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "data" / "scenarios"))
    args = ap.parse_args()

    cfg, art, df = load_inputs()
    warmup = int(art["required_history_ticks"]) + 4
    print(f"warmup_ticks = {warmup} "
          f"(required_history_ticks {art['required_history_ticks']} + 4)")

    builders = {"A": build_a, "B": build_b, "C": build_c, "D": build_d,
                "E": build_e, "F": build_f}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for sid in "ABCDEF":
        scenario = builders[sid](cfg, art, df, warmup)
        save_manifest(scenario, out_dir / f"{sid}.json")
        n = len(df.loc[scenario.window["start"]:scenario.window["end"]]) \
            + warmup
        print(f"[OK] {sid}: {out_dir / (sid + '.json')} "
              f"({n} stream ticks, window {scenario.window['start']} .. "
              f"{scenario.window['end']})")
    print("all six manifests written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
