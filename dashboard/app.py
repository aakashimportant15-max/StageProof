"""StageProof dashboard — Streamlit shell (DESIGN §6).

Drives the real engine (Runner) exactly like scripts/run_scenario.py: the
dashboard never re-implements decisions. It steps the scenario stream on
demand (Play · Step · Jump), renders stored TickRecord fields only, and
labels every surface as simulated/offline.

Run:  python -m streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DASH = Path(__file__).resolve().parent
for _p in (str(ROOT), str(DASH)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evidence_view  # noqa: E402
import live  # noqa: E402
import plots  # noqa: E402
import proof  # noqa: E402

from stageproof.data import TICK_MINUTES, load_model_artifact  # noqa: E402
from stageproof.domain import VolunteerReply  # noqa: E402
from stageproof.models import ModelBundle  # noqa: E402
from stageproof.pipeline import Runner  # noqa: E402
from stageproof.security.audit import AuditLog, verify as audit_verify  # noqa: E402
from stageproof.security.transport import load_keys  # noqa: E402
from stageproof.settings import load_settings  # noqa: E402
from stageproof.sim.scenarios import ScenarioStream, load_manifest  # noqa: E402

st.set_page_config(page_title="StageProof — offline demo",
                   page_icon="🌊", layout="wide")

SCENARIO_LABELS = {
    "A": "A · Real flood — everything agrees",
    "B": "B · Sensor fault — gauge B gets stuck",
    "C": "C · Fabricated flood — valid key, no physical support",
    "D": "D · Suppressed flood — valid key, real flood hidden",
    "E": "E · Uncertain — upstream telemetry lost during a rise",
    "F": "F · Unsigned & replayed packets — transport attack",
}

SENSOR_COLORS = {
    "TRUSTED": plots.COLORS["normal"],
    "SUSPECT": plots.COLORS["fault"],
    "QUARANTINED": plots.COLORS["quarantine"],
    "RECOVERING": plots.COLORS["uncertain"],
}


# ---------------------------------------------------------------------------
# cached engine assets (read-only for the session)
# ---------------------------------------------------------------------------

def _scenario_spans(manifests: dict) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Merged UTC spans [start − warmup .. end] of every loaded manifest."""
    spans = []
    for manifest in manifests.values():
        start = pd.Timestamp(str(manifest.window["start"]))
        end = pd.Timestamp(str(manifest.window["end"]))
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        if end.tzinfo is None:
            end = end.tz_localize("UTC")
        warm = start.tz_convert("UTC") - pd.Timedelta(
            minutes=TICK_MINUTES * int(manifest.warmup_ticks))
        spans.append((warm, end.tz_convert("UTC")))
    spans.sort()
    merged: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _load_reach_windowed(path: Path, manifests: dict) -> pd.DataFrame:
    """Read only the reach.csv rows the loaded scenario windows need.

    The whole 42 MB / 590k-row file peaks ~200 MB and can raise MemoryError
    on a loaded Windows session; each ScenarioStream slices its own window
    anyway, so this keeps the union of the scenario spans only. Identical
    columns, dtypes and UTC index as the whole-file read."""
    spans = _scenario_spans(manifests)
    if not spans:
        return pd.read_csv(path, index_col="ts_utc", parse_dates=True)
    lo = min(s for s, _ in spans)
    hi = max(e for _, e in spans)
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, chunksize=100_000):
        ts = pd.to_datetime(chunk.pop("ts_utc"), utc=True, format="ISO8601")
        if ((ts >= lo) & (ts <= hi)).any():
            keep = (ts >= spans[0][0]) & (ts <= spans[0][1])
            for s, e in spans[1:]:
                keep |= (ts >= s) & (ts <= e)
            if keep.any():
                part = chunk.loc[keep].copy()
                part.insert(0, "ts_utc", ts[keep])
                parts.append(part)
        if ts.iloc[-1] > hi:
            break  # regular monotonic grid: later rows cannot match
    if not parts:  # window spans outside the file — behave as before the fix
        return pd.read_csv(path, index_col="ts_utc", parse_dates=True)
    df = pd.concat(parts, ignore_index=True)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df = df.set_index("ts_utc").sort_index()
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_resource(show_spinner=False)
def load_assets() -> dict:
    settings = load_settings(ROOT / "config", env_path=ROOT / ".env")
    art = load_model_artifact(ROOT / "artifacts" / "model.json")
    settings.attach_model(art)
    bundle = ModelBundle.from_dict(art)
    keys = load_keys(settings.env, settings.reach.stations)
    # mirror run_scenario.py: size the NOISE check against the fitted baseline
    stations = {role: dict(p) for role, p in settings.reach.stations.items()}
    for role, sp in art.get("station_params", {}).items():
        if role in stations and "noise_baseline_std" in sp:
            stations[role]["noise_baseline_std"] = float(
                sp["noise_baseline_std"])
    manifests: dict = {}
    for sid in SCENARIO_LABELS:
        path = ROOT / "data" / "scenarios" / f"{sid}.json"
        if path.exists():
            try:
                manifests[sid] = load_manifest(path)
            except Exception:  # a broken manifest must not kill the app
                continue
    df = _load_reach_windowed(ROOT / "data" / "reach.csv", manifests)
    return {"settings": settings, "bundle": bundle, "df": df, "keys": keys,
            "stations": stations, "manifests": manifests}


# ---------------------------------------------------------------------------
# run lifecycle (mirrors scripts/run_scenario.py)
# ---------------------------------------------------------------------------

def start_run(sid: str, assets: dict) -> dict:
    manifest = assets["manifests"][sid]
    stream = ScenarioStream(manifest, assets["df"], assets["stations"],
                            assets["keys"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = (ROOT / "artifacts" / "audit"
                  / f"dashboard_{manifest.id}_{stamp}.jsonl")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    runner = Runner(assets["settings"], assets["bundle"], AuditLog(audit_path))
    first_ts, _ = stream.tick(0)
    runner.start(manifest.id, manifest.seed, ts=first_ts.ts)
    return {
        "sid": manifest.id, "manifest": manifest, "stream": stream,
        "runner": runner, "audit_path": audit_path,
        "next_idx": 0, "records": [], "rain": [], "pending_replies": [],
        "playing": False, "finished": False,
        "reveal_truth": False, "autopilot": True, "speed": 8, "error": None,
    }


def _advance(run: dict, steps: int = 1) -> None:
    """Step the engine `steps` ticks — identical to the CLI driver loop."""
    stream, runner = run["stream"], run["runner"]
    try:
        for _ in range(int(steps)):
            idx = run["next_idx"]
            if idx >= len(stream):
                break
            tick_input, truth = stream.tick(idx)
            if run["autopilot"]:
                for op in stream.operator_events_at(idx):
                    if op.get("kind") == "ACK_RELEASE":
                        runner.ack_release(str(op.get("officer", "OPERATOR")))
                run["pending_replies"].extend(stream.volunteer_replies_at(idx))
                open_round = None
                if run["records"]:
                    ver = run["records"][-1].verification
                    if ver and ver.get("status") == "OPEN":
                        open_round = ver.get("round_id")
                if open_round:
                    for vrep in run["pending_replies"]:
                        runner.submit_verification_reply(VolunteerReply(
                            ts=tick_input.ts,
                            volunteer_id=str(vrep["volunteer_id"]),
                            code=int(vrep["code"]), round_id=open_round))
                    run["pending_replies"] = []
            else:
                # manual mode still queues scripted replies for the UI buttons
                run["pending_replies"].extend(stream.volunteer_replies_at(idx))
            rec = runner.step(tick_input)
            rec.truth = truth
            run["records"].append(rec)
            run["rain"].append(tick_input.rain_prev_hr_mm)
            run["next_idx"] = idx + 1
    except Exception as exc:  # never crash the whole app on one bad tick
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["playing"] = False
    if run["next_idx"] >= len(stream) and not run["finished"]:
        run["finished"] = True
        run["playing"] = False
        try:
            runner.finish()
        except Exception as exc:
            run["error"] = f"{type(exc).__name__}: {exc}"


def _event_sig(rec) -> tuple:
    v = rec.verdict
    return (v.label.value if v else None, v.subtype if v else None,
            rec.alert_state.value, rec.alert_source,
            tuple(sorted((role, s.state.value)
                         for role, s in rec.sensor_status.items())))


def _is_event(prev, rec) -> bool:
    if prev is None or _event_sig(prev) != _event_sig(rec):
        return True
    return bool(rec.new_actions or rec.new_messages)


def _jump(run: dict) -> None:
    n = len(run["stream"])
    while run["next_idx"] < n:
        prev = run["records"][-1] if run["records"] else None
        _advance(run, 1)
        rec = run["records"][-1]
        if prev is None or _is_event(prev, rec):
            break


def _advance_to_end(run: dict) -> None:
    while run["next_idx"] < len(run["stream"]) and run["error"] is None:
        _advance(run, 1)


def _open_round(run: dict) -> dict | None:
    if not run["records"]:
        return None
    ver = run["records"][-1].verification
    if ver and ver.get("status") == "OPEN":
        return ver
    return None


def _submit_reply(run: dict, vrep: dict) -> tuple[bool, str]:
    ver = _open_round(run)
    if ver is None:
        return False, "no open verification round"
    ts = run["records"][-1].ts
    accepted, detail = run["runner"].submit_verification_reply(VolunteerReply(
        ts=ts, volunteer_id=str(vrep["volunteer_id"]),
        code=int(vrep["code"]), round_id=str(ver["round_id"])))
    if accepted:
        run["pending_replies"] = [p for p in run["pending_replies"]
                                  if p is not vrep]
    return accepted, detail


def _ack_release(run: dict) -> None:
    run["runner"].ack_release("OFFICER")


# ---------------------------------------------------------------------------
# context handed to the view modules
# ---------------------------------------------------------------------------

def _injected(manifest) -> list[dict]:
    out = []
    for c in manifest.corruptions:
        out.append({
            "start_tick": int(c.get("start_tick", 0)),
            "end_tick": int(c.get("end_tick", c.get("start_tick", 0))),
            "type": f"{c.get('role', '?')} · {c.get('type', '')}".strip(),
        })
    for a in manifest.transport_attacks:
        t = int(a.get("tick", 0))
        out.append({"start_tick": t, "end_tick": t,
                    "type": f"{a.get('role', '?')} · {a.get('type', '')}".strip()})
    return out


def _ctx(run: dict) -> dict:
    a = load_assets()
    records = run["records"]
    controls = {
        "approve": lambda aid: (run["runner"].approve(aid, "OFFICER"),
                                st.rerun()),
        "reject": lambda aid: (run["runner"].reject(aid, "OFFICER"),
                               st.rerun()),
        "ack_release": lambda: (_ack_release(run),
                                st.rerun()),
        "officer_confirm": lambda: (run["runner"].officer_confirm(),
                                    st.rerun()),
        "officer_clear": lambda: (run["runner"].officer_clear(),
                                  st.rerun()),
        "submit_reply": lambda vrep: _submit_reply(run, vrep),
    }
    return {
        "run": run, "records": records, "rain": run["rain"],
        "cursor": max(len(records) - 1, 0), "manifest": run["manifest"],
        "settings": a["settings"], "bundle": a["bundle"], "keys": a["keys"],
        "levels": a["settings"].reach.levels.get("B", {}),
        "band_z": float(a["settings"].threshold("context", "band_z")),
        "ts_skew": float(a["settings"].threshold("transport",
                                                 "ts_skew_max_seconds")),
        "reveal_truth": run["reveal_truth"], "autopilot": run["autopilot"],
        "injected": _injected(run["manifest"]),
        "controls": controls, "colors": plots.COLORS,
    }


# ---------------------------------------------------------------------------
# sidebar (full-rerun controls)
# ---------------------------------------------------------------------------

def _sidebar(assets: dict, run: dict | None) -> None:
    sb = st.sidebar
    sb.markdown("### StageProof")
    sb.caption("Offline replay · real engine · simulated data")

    options = sorted(assets["manifests"])
    idx = options.index(run["sid"]) if run and run["sid"] in options else 0
    sid = sb.selectbox("Scenario", options, index=idx,
                       format_func=lambda s: SCENARIO_LABELS.get(s, s))
    if run is not None and sid != run["sid"]:
        sb.warning(f"Selected {sid} — press Start / restart to load it.")

    if sb.button("▶ Start / restart run", type="primary", width="stretch"):
        with st.spinner(f"Warming up {assets['manifests'][sid].warmup_ticks} "
                        "ticks before the event window…"):
            new_run = start_run(sid, assets)
            _advance(new_run, int(new_run["manifest"].warmup_ticks))
            st.session_state.run = new_run
        st.rerun()

    if run is None:
        return

    playing = run["playing"] and not run["finished"]
    c1, c2 = sb.columns(2)
    if c1.button("⏸ Pause" if playing else "▶ Play", width="stretch",
                 disabled=run["finished"] or run["error"] is not None):
        run["playing"] = not playing
        st.rerun()
    if c2.button("＋1 tick", width="stretch",
                 disabled=playing or run["finished"]):
        _advance(run, 1)
        st.rerun()
    if sb.button("⤼ Jump to next event", width="stretch",
                 disabled=playing or run["finished"]):
        with st.spinner("Stepping to the next verdict / alert / action change…"):
            _jump(run)
        st.rerun()
    if sb.button("⏭ Run to end", width="stretch",
                 disabled=playing or run["finished"]):
        with st.spinner("Stepping to the end of the scenario…"):
            _advance_to_end(run)
        st.rerun()

    speed = sb.segmented_control("Playback speed (ticks/second)",
                                 [2, 8, 24], default=run["speed"],
                                 format_func=lambda v: f"{v}×")
    if speed:
        run["speed"] = int(speed)

    run["reveal_truth"] = sb.toggle(
        "Reveal ground truth (evaluation)", value=run["reveal_truth"],
        help="Shows the simulation's true stage and the injected-fault spans. "
             "StageProof itself never sees these.")
    run["autopilot"] = sb.toggle(
        "Autopilot — scripted replies & officer ACK", value=run["autopilot"],
        help="OFF: scripted volunteer replies queue as buttons and the "
             "officer acknowledge is a button you press. ON mirrors "
             "scripts/run_scenario.py.")

    if assets["settings"].policy.demo_auto_approve_tier1:
        sb.caption("Policy: tier-1 actions auto-approved in demo mode.")
    sb.caption(f"Audit log: {run['audit_path'].name}")
    if run["error"]:
        sb.error(run["error"])


# ---------------------------------------------------------------------------
# header strip (updates on every fragment run)
# ---------------------------------------------------------------------------

def _chip(col, title: str, value: str, color: str | None = None,
          sub: str | None = None) -> None:
    color = color or plots.COLORS["text"]
    sub_html = (f"<div style='color:{plots.COLORS['muted']};"
                f"font-size:0.68rem;margin-top:1px'>{sub}</div>"
                if sub else "")
    col.markdown(
        f"<div style='background:{plots.COLORS['panel']};"
        f"border:1px solid {plots.COLORS['border']};border-radius:6px;"
        f"padding:6px 10px;min-height:52px'>"
        f"<div style='color:{plots.COLORS['muted']};font-size:0.66rem;"
        f"text-transform:uppercase;letter-spacing:.05em'>{title}</div>"
        f"<div style='color:{color};font-weight:600;font-size:0.88rem;"
        f"line-height:1.25'>{value}</div>{sub_html}</div>",
        unsafe_allow_html=True)


def _header(run: dict) -> None:
    records = run["records"]
    last = records[-1] if records else None
    n = len(run["stream"])
    cols = st.columns([2.4, 1.5, 1.4, 1.9, 1.5, 1.2, 1.9])

    state = ("finished" if run["finished"]
             else f"▶ {run['speed']}×" if run["playing"] else "❚❚ paused")
    _chip(cols[0], "Scenario", SCENARIO_LABELS.get(run["sid"], run["sid"]),
          sub=f"{run['manifest'].title} · {state}")
    clock = last.ts.strftime("%d %b %H:%M") if last is not None else "—"
    _chip(cols[1], "Simulated clock (UTC)", clock)
    _chip(cols[2], "Tick", f"{last.tick_idx + 1 if last else 0} / {n}",
          sub=f"warm-up {run['manifest'].warmup_ticks} ticks")
    v = last.verdict if last else None
    label = v.label.value if v else "—"
    _chip(cols[3], "Verdict",
          label + (f" · {v.subtype}" if v and v.subtype else ""),
          color=plots.VERDICT_COLORS.get(label))
    alert = last.alert_state.value if last else "—"
    _chip(cols[4], "Alert",
          alert + (f" · {last.alert_source}" if last and last.alert_source
                   else ""))
    stb = last.sensor_status.get("B") if last else None
    _chip(cols[5], "Sensor B",
          stb.state.value if stb else "—",
          color=SENSOR_COLORS.get(stb.state.value) if stb else None)
    res = audit_verify(run["audit_path"])
    _chip(cols[6], "Audit chain",
          f"✓ verified · {res.n_events} events" if res.ok else "✗ BROKEN",
          color=plots.COLORS["normal"] if res.ok else plots.COLORS["attack"],
          sub=run["audit_path"].name)


# ---------------------------------------------------------------------------
# stage (fragment: timer playback + all three tabs)
# ---------------------------------------------------------------------------

def _delay(run: dict) -> float | None:
    if not run["playing"] or run["finished"]:
        return None
    return max(1.0 / float(run["speed"]), 0.05)


def render_stage() -> None:
    run = st.session_state.get("run")
    if run is None:
        return
    if run["playing"] and not run["finished"]:
        _advance(run, 1)
        if run["finished"]:
            st.rerun()   # re-register the fragment without a timer
            return
    _header(run)
    ctx = _ctx(run)
    t1, t2, t3 = st.tabs(["Live operations", "Incident & evidence",
                          "Proof & audit"])
    with t1:
        live.render(ctx)
    with t2:
        evidence_view.render(ctx)
    with t3:
        proof.render(ctx)


# ---------------------------------------------------------------------------
# banner + start screen + main
# ---------------------------------------------------------------------------

def _banner() -> None:
    st.markdown(
        f"""
        <div style="border-left:4px solid {plots.COLORS['fault']};
                    background:rgba(230,159,0,0.08);border-radius:6px;
                    padding:10px 14px;margin-bottom:8px;">
          <div style="color:{plots.COLORS['fault']};font-weight:700;
                      font-size:0.85rem;letter-spacing:.05em;">
            SIMULATED DATA · OFFLINE DEMO — NOT AN OPERATIONAL WARNING SYSTEM
          </div>
          <div style="color:{plots.COLORS['muted']};font-size:0.8rem;
                      margin-top:4px;">
            Recorded river data replayed through the real StageProof engine
            with injected sensor faults and transport attacks. Alerts here are
            simulated messages to a fake phone simulator; nothing leaves this
            machine and no network is used. Ground truth appears only behind
            the sidebar's “Reveal ground truth” toggle — StageProof never
            sees it.
          </div>
        </div>
        """, unsafe_allow_html=True)


def _start_screen(assets: dict) -> None:
    st.title("StageProof")
    st.markdown("**A cyber-physical integrity layer for flood early "
                "warning** — offline replay demo of the full engine.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            "**What runs**\n\n"
            "Signed telemetry is replayed tick-by-tick through the real "
            "engine: transport checks → model expectations → evidence → "
            "verdict → response → hash-chained audit. Nothing is scripted "
            "in the UI.")
    with c2:
        st.markdown(
            "**Try these scenarios**\n\n"
            "- **C** fabricated flood (valid key, no support)\n"
            "- **D** suppressed flood (real flood hidden)\n"
            "- **F** unsigned & replayed packets\n\n"
            "Press Play, Step, or Jump to next event in the sidebar.")
    with c3:
        st.markdown(
            "**Safety posture**\n\n"
            "Unknown or unverifiable data can never produce a public "
            "warning. Every commit, action and officer decision is written "
            "to a tamper-evident audit log you can verify in the Proof tab.")
    st.info("Pick a scenario in the sidebar and press "
            "**Start / restart run** — warm-up ticks run first, then the "
            "event window starts.")


def main() -> None:
    assets = load_assets()
    run = st.session_state.get("run")
    _banner()
    _sidebar(assets, run)
    run = st.session_state.get("run")
    if run is None:
        _start_screen(assets)
        return
    stage = st.fragment(run_every=_delay(run), key="stage")(render_stage)
    stage()


if __name__ == "__main__":  # Streamlit executes this script as __main__
    main()
