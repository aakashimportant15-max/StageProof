"""Run one scenario end-to-end from the CLI (TASKS T041, Gate G3 T042).

Loads the manifest from data/scenarios/<ID>.json, replays it tick-by-tick
through the real Runner (transport verify -> models -> evidence -> decision
-> response -> audit), drives the volunteer/operator autopilot unless
--no-autopilot, then machine-checks the manifest's `expected` block.

Exit code is 0 only when every expectation passes.

Usage:
  python scripts/run_scenario.py --scenario A
  python scripts/run_scenario.py --scenario F --explain 24
  python scripts/run_scenario.py --scenario D --export artifacts/exports
  python scripts/run_scenario.py --scenario E --no-autopilot
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stageproof.data import load_model_artifact
from stageproof.domain import Label, VolunteerReply, record_to_dict
from stageproof.models import ModelBundle
from stageproof.pipeline import Runner
from stageproof.security.audit import AuditLog
from stageproof.security.transport import load_keys
from stageproof.settings import load_settings
from stageproof.sim.scenarios import (ScenarioStream, evaluate_expectations,
                                      load_manifest)


def _fmt_ts(ts) -> str:
    return ts.strftime("%m-%d %H:%M")


def _timeline(records) -> None:
    """Compact timeline: print only ticks where something changed."""
    prev_v = prev_alert = None
    prev_states: dict[str, str] = {}
    for rec in records:
        parts: list[str] = []
        v = rec.verdict
        sig = (v.label.value if v else None, v.subtype if v else None)
        if sig != prev_v:
            conf = v.confidence.value if v else "-"
            sub = f"/{v.subtype}" if (v and v.subtype) else ""
            parts.append(f"VERDICT={v.label.value}{sub}({conf})"
                         if v else "VERDICT=-")
            prev_v = sig
        alert_sig = (rec.alert_state.value, rec.alert_source)
        if alert_sig != prev_alert:
            src = f" <- {rec.alert_source}" if rec.alert_source else ""
            parts.append(f"ALERT={rec.alert_state.value}{src}")
            prev_alert = alert_sig
        states = {r: st.state.value for r, st in rec.sensor_status.items()}
        if states != prev_states:
            diff = {r: s for r, s in states.items()
                    if prev_states.get(r) != s}
            parts.append("SENSOR " + " ".join(f"{r}={s}"
                                              for r, s in sorted(diff.items())))
            prev_states = states
        if rec.new_actions:
            acts = ",".join(sorted({a.type.value
                                    for a in rec.new_actions}))
            parts.append(f"ACT[{acts}]")
        if not parts:
            continue
        line = f"t{rec.tick_idx:4d} {_fmt_ts(rec.ts)}  " + "  ".join(parts)
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"))


def _explain(records, tick: int) -> None:
    if not 0 <= tick < len(records):
        print(f"EXPLAIN: tick {tick} outside 0..{len(records) - 1}")
        return
    rec = records[tick]
    print(f"--- tick {tick}  {_fmt_ts(rec.ts)} ---")
    print(f"accepted: {rec.accepted}")
    print(f"transport_flags: { {r: list(f) for r, f in rec.transport_flags.items()} }")
    print(f"transport_hard: { {r: list(f) for r, f in (rec.evidence.transport_hard if rec.evidence else {}).items()} }")
    ev = rec.evidence
    if ev is not None:
        print(f"obs_stage={ev.obs_stage} pred_stage={ev.pred_stage} "
              f"notable={ev.notable}")
        print(f"z={ev.z} z_mean={ev.z_mean} context={ev.context.value} "
              f"up_trend={ev.up_trend.value} rain={ev.rain.value} "
              f"downstream={ev.downstream.value} "
              f"z_down_mean={ev.z_down_mean}")
        print(f"shape_ok={ev.shape_ok} rate_exceeded={ev.rate_exceeded} "
              f"variant={ev.variant} replay_match={ev.replay_match} "
              f"noise_too_clean={ev.noise_too_clean} drift={ev.drift}")
        print(f"health_flags: { {r: list(f) for r, f in ev.health_flags.items()} }")
        print(f"trusted_inputs: {ev.trusted_inputs}")
    v = rec.verdict
    if v is not None:
        print(f"candidate={v.candidate.value}/{v.candidate_subtype} "
              f"committed={v.label.value}/{v.subtype} "
              f"conf={v.confidence.value} rule={v.rule_id}")
        for reason in v.reasons:
            print(f"  reason: {reason}")
        print(f"persist {v.persist_count}/{v.persist_needed} "
              f"changed={v.changed}")
    if rec.estimate is not None and rec.estimate.in_use:
        est = rec.estimate
        print(f"estimate IN USE: stage={est.stage_hat:.2f} "
              f"band=[{est.stage_lo:.2f}, {est.stage_hi:.2f}] "
              f"conf={est.confidence.value}")
    if rec.baselines:
        print(f"baselines: {rec.baselines}")
    if rec.verification:
        print(f"verification: {rec.verification}")
    kinds = [e.kind for e in rec.audit_events_new]
    print(f"audit events this tick: {kinds}")
    print(f"halted_audit={rec.halted_audit}")


def _export(records, out_dir: Path, sid: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"scenario_{sid}_records.jsonl"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(record_to_dict(rec)) + "\n")
    return path


def _audit_checks(audit_path: Path) -> bool:
    from stageproof.security import audit as audit_mod
    ok = True
    result = audit_mod.verify(audit_path)
    if result.ok:
        print(f"[OK] audit chain verifies: {result.n_events} events")
    else:
        ok = False
        print(f"[FAIL] audit chain BROKE at index {result.first_bad_index}: "
              f"{result.reason}")
    # tamper demo on a COPY of the log (never the live file)
    tmp = Path(tempfile.mkdtemp(prefix="stageproof_tamper_"))
    try:
        copy = tmp / "tampered.jsonl"
        shutil.copyfile(audit_path, copy)
        lines = copy.read_text(encoding="utf-8").splitlines()
        if lines:
            raw = lines[0]
            pos = raw.find('"kind"')
            flip = raw[:pos + 9] + ("X" if raw[pos + 9:pos + 10] != "X"
                                    else "Y") + raw[pos + 10:]
            lines[0] = flip
            copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
            t = audit_mod.verify(copy)
            if t.ok:
                ok = False
                print("[FAIL] tamper NOT detected in modified copy")
            else:
                print(f"[OK] tamper detected in copy: {t.reason}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one StageProof scenario")
    ap.add_argument("--scenario", required=True,
                    help="scenario id (manifest data/scenarios/<ID>.json)")
    ap.add_argument("--explain", type=int, default=None, metavar="TICK",
                    help="print stored Evidence and Verdict for TICK")
    ap.add_argument("--export", default=None, metavar="DIR",
                    help="write per-tick TickRecord JSONL to DIR")
    ap.add_argument("--no-autopilot", action="store_true",
                    help="do not auto-submit volunteer/operator events")
    args = ap.parse_args()
    sid = args.scenario.upper()

    settings = load_settings(ROOT / "config", env_path=ROOT / ".env")
    art = load_model_artifact(ROOT / "artifacts" / "model.json")
    settings.attach_model(art)
    bundle = ModelBundle.from_dict(art)
    df = pd.read_csv(ROOT / "data" / "reach.csv",
                     index_col="ts_utc", parse_dates=True)
    manifest = load_manifest(ROOT / "data" / "scenarios" / f"{sid}.json")
    keys = load_keys(settings.env, settings.reach.stations)

    # The NOISE_BURST adversary sizes its burst against the same baseline the
    # engine's NOISE check divides by (the fitted station param), so detection
    # strength cannot silently drift when the fit derivation changes.
    stations = {role: dict(p) for role, p in settings.reach.stations.items()}
    for role, sp in art.get("station_params", {}).items():
        if role in stations and "noise_baseline_std" in sp:
            stations[role]["noise_baseline_std"] = float(sp["noise_baseline_std"])

    stream = ScenarioStream(manifest, df, stations, keys)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = ROOT / "artifacts" / "audit" / f"scenario_{sid}_{stamp}.jsonl"
    runner = Runner(settings, bundle, AuditLog(audit_path))

    print(f"scenario {manifest.id}: {manifest.title}")
    print(f"  window {manifest.window['start']} .. {manifest.window['end']}  "
          f"warmup={manifest.warmup_ticks} seed={manifest.seed} "
          f"ticks={len(stream)}  audit={audit_path.name}")
    first_ts, _ = stream.tick(0)
    runner.start(manifest.id, manifest.seed, ts=first_ts.ts)

    records = []
    pending_replies: list[dict] = []
    autopilot = not args.no_autopilot
    for tick_input, truth in stream:
        if autopilot:
            t = tick_input.tick_idx
            for op in stream.operator_events_at(t):
                if op.get("kind") == "ACK_RELEASE":
                    runner.ack_release(str(op.get("officer", "OPERATOR")))
            pending_replies.extend(stream.volunteer_replies_at(t))
            open_round = None
            if records:
                ver = records[-1].verification
                if ver and ver.get("status") == "OPEN":
                    open_round = ver.get("round_id")
            if open_round:
                for vrep in pending_replies:
                    runner.submit_verification_reply(
                        VolunteerReply(ts=tick_input.ts,
                                       volunteer_id=str(vrep["volunteer_id"]),
                                       code=int(vrep["code"]),
                                       round_id=open_round))
                pending_replies = []
        rec = runner.step(tick_input)
        rec.truth = truth
        records.append(rec)
    runner.finish()

    print("--- timeline (changes only) ---")
    _timeline(records)
    if args.explain is not None:
        print("--- explain ---")
        _explain(records, args.explain)

    exported = None
    if args.export:
        exported = _export(records, ROOT / args.export, sid)
        print(f"[OK] exported {len(records)} records -> {exported}")

    audit_ok = _audit_checks(audit_path)

    print("--- expectations ---")
    results = evaluate_expectations(manifest, records)
    all_pass = True
    for res in results:
        mark = "PASS" if res.passed else "FAIL"
        print(f"[{mark}] {res.id}: {res.detail}")
        all_pass = all_pass and res.passed
    n_truth_bad = sum(
        1 for rec in records
        if rec.truth is not None and rec.verdict is not None
        and rec.truth.truth_notable
        and rec.truth.truth_label == "REAL_FLOOD"
        and rec.verdict.label == Label.POSSIBLE_CYBER_ATTACK)
    if n_truth_bad:
        print(f"note: {n_truth_bad} ticks flagged ATTACK while truth was "
              f"REAL_FLOOD (false alarms vs truth layer)")

    ok = all_pass and audit_ok
    print(f"RESULT scenario {sid}: "
          f"{'PASS' if ok else 'FAIL'} "
          f"({sum(1 for r in results if r.passed)}/{len(results)} "
          f"expectations, audit {'ok' if audit_ok else 'BROKEN'})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
