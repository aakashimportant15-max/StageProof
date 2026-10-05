"""Proof & audit tab (DESIGN §8.2): packet integrity, sequence protection,
the hash-chained audit log, and the manifest's automatic scenario checks.

Display-only against the same stored run the other tabs read. Tamper
demonstrations are performed on COPIES only: a signed reading is rebuilt in
memory (the live packet is never modified) and the audit log is altered via
stageproof.security.audit.tamper_copy, which never touches the original
file — the copy is deleted again after the check.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pandas as pd
import streamlit as st

import plots

from stageproof.security import audit as audit_mod
from stageproof.security.transport import (canonical_string,
                                           verify_stateless)
from stageproof.sim.scenarios import evaluate_expectations


def _e(x) -> str:
    return x.value if hasattr(x, "value") else ("—" if x is None else str(x))


def _packet_section(ctx: dict) -> None:
    records = ctx["records"]
    st.markdown("#### 1 · Packet integrity — tampering with a signed reading")
    st.caption("Every packet is HMAC-SHA256 signed by its station key. The "
               "demo below alters an in-memory COPY of one stored packet and "
               "re-checks the signature — the live run is never touched.")

    roles = [r for r in ("A", "T", "B", "C")
             if any((rec.readings or {}).get(r) is not None
                    for rec in records)]
    if not roles:
        st.caption("No packets in this run yet.")
        return
    role = st.selectbox("Channel", roles,
                        index=roles.index("B") if "B" in roles else 0,
                        key="proof_role")
    ticks = [rec.tick_idx for rec in records
             if (rec.readings or {}).get(role) is not None]
    if not ticks:
        return
    rejected = [t for t in ticks
                if not (records[t].accepted or {}).get(role, True)]
    default = rejected[-1] if rejected else ticks[-1]
    tick = st.selectbox("Tick", ticks, index=ticks.index(default),
                        format_func=lambda t: (
                            f"t{t} · {records[t].ts.strftime('%H:%M')} · "
                            f"seq {records[t].readings[role].seq}"
                            + ("" if (records[t].accepted or {}).get(
                                role, True) else " · REJECTED")),
                        key=f"proof_tick_{role}")
    rec = records[tick]
    reading = rec.readings[role]

    alter = st.radio(
        "Alter the copy before re-checking",
        ["unmodified", "stage +5.00 ft", f"station id → "
         f"{'T' if role != 'T' else 'A'}", "timestamp +30 min"],
        horizontal=True, key="proof_alter")
    probe = reading
    if alter == "stage +5.00 ft":
        probe = replace(reading, stage=reading.stage + 5.0)
    elif alter.startswith("station id"):
        probe = replace(reading, station_id="T" if role != "T" else "A")
    elif alter == "timestamp +30 min":
        probe = replace(reading, ts=reading.ts + timedelta(minutes=30))

    stored_flags = list((rec.transport_flags or {}).get(role, ()))
    stored_ok = (rec.accepted or {}).get(role, True)
    verdict = verify_stateless(probe, role, rec.ts, ctx["keys"],
                               ctx["ts_skew"])
    stateless_covers = not (stored_flags and "SEQ" in
                            " ".join(stored_flags))

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Canonical string (what is signed)**")
        st.code(canonical_string(probe.station_id, probe.ts, probe.seq,
                                 probe.stage, probe.unit), language=None)
        st.caption(f"signature {reading.sig[:24]}… (HMAC-SHA256)")
    with c2:
        st.markdown("**Re-check result**")
        st.markdown(
            f"- stored outcome: "
            + (f"accepted{' · flags: ' + ', '.join(stored_flags) if stored_flags else ''}"
               if stored_ok else
               f"rejected · flags: {', '.join(stored_flags)}")
            + f"\n- recheck of the {'unmodified' if alter == 'unmodified' else 'altered'} copy: "
            + ("**accepted** — signature, station binding and timestamp all check out"
               if verdict.accepted else
               f"**REJECTED — {', '.join(verdict.hard)}**"))
        if not stateless_covers:
            st.caption("The stored rejection was a stateful check "
                       "(sequence/monotonic time); the stateless recheck "
                       "covers signature, binding and skew only.")
        if not verdict.accepted and alter == "unmodified":
            st.caption("This packet was stored as rejected; note that "
                       "replay/sequence rejections are stateful and only "
                       "appear in the stored outcome.")


def _signature_not_truth(ctx: dict) -> None:
    st.markdown("#### 2 · A valid signature is not a true reading")
    rec = None
    for r in ctx["records"]:
        ev = r.evidence
        if ev is not None and (r.accepted or {}).get("B") \
                and _e(ev.context) == "PHANTOM":
            rec = r
            break
    if rec is None:
        st.caption("This run has no signed-but-physically-unsupported "
                   "reading. Run scenario C to see one: the packet passes "
                   "transport, then fails the physics check.")
        return
    ev = rec.evidence
    z = f"{ev.z_mean:+.2f}σ" if ev.z_mean is not None else "—"
    c1, c2 = st.columns(2)
    c1.markdown(
        f"<div style='background:{plots.COLORS['panel']};"
        f"border-left:4px solid {plots.COLORS['normal']};border-radius:6px;"
        f"padding:10px 12px'>"
        f"<b>Transport layer · tick {rec.tick_idx} "
        f"({rec.ts.strftime('%H:%M')})</b><br>"
        f"<span style='color:{plots.COLORS['normal']}'>ACCEPTED</span> — "
        f"valid signature, correct station, fresh sequence. The packet is "
        f"authentic.</div>", unsafe_allow_html=True)
    c2.markdown(
        f"<div style='background:{plots.COLORS['panel']};"
        f"border-left:4px solid {plots.COLORS['attack']};border-radius:6px;"
        f"padding:10px 12px'>"
        f"<b>Physics layer · same tick</b><br>"
        f"<span style='color:{plots.COLORS['attack']}'>PHANTOM</span> — "
        f"observed {ev.obs_stage:.2f} ft vs expected "
        f"{ev.pred_stage:.2f} ft ({z}); upstream and rainfall cannot "
        f"explain it. An authentic packet carrying a false reading.</div>",
        unsafe_allow_html=True)
    st.caption("Cybersecurity starts after authentication: StageProof treats "
               "authenticated-but-implausible as evidence, not as truth.")


def _sequence_section(ctx: dict) -> None:
    records = ctx["records"]
    st.markdown("#### 3 · Sequence & replay protection")
    st.plotly_chart(plots.seq_figure(records), theme=None, width="stretch")
    rows = []
    for rec in records:
        for r, flags in (rec.transport_flags or {}).items():
            if flags:
                rows.append({"tick": rec.tick_idx,
                             "time": rec.ts.strftime("%H:%M"), "role": r,
                             "flags": ", ".join(flags),
                             "accepted": (rec.accepted or {}).get(r, True)})
    rows = rows[-10:]
    if rows:
        st.markdown("**Recent transport flags**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("No transport flags so far. Run scenario F to see "
                   "unsigned and replayed packets rejected (✗ markers on "
                   "the chart above).")


def _audit_section(ctx: dict) -> None:
    run = ctx["run"]
    records = ctx["records"]
    st.markdown("#### 4 · Audit trail — hash-chained log")
    st.caption("Each event stores the hash of the previous one (SHA-256). "
               "Altering any event breaks every hash after it.")

    if st.button("Verify the live chain", key="proof_verify",
                 width="stretch"):
        res = audit_mod.verify(run["audit_path"])
        st.session_state["proof_verify_result"] = (
            ("ok", res.n_events, None) if res.ok
            else ("bad", res.n_events, f"broken at index "
                  f"{res.first_bad_index}: {res.reason}"))
    stored = st.session_state.get("proof_verify_result")
    if stored is None:
        res = audit_mod.verify(run["audit_path"])
        stored = (("ok", res.n_events, None) if res.ok
                  else ("bad", res.n_events,
                        f"broken at index {res.first_bad_index}"))
        st.session_state["proof_verify_result"] = stored
    if stored[0] == "ok":
        st.success(f"Chain verifies — {stored[1]} events, no gaps.")
    else:
        st.error(f"Chain BROKEN — {stored[2]}")

    if st.button("Tamper with a COPY of the log (the live file is never "
                 "touched)", key="proof_tamper", width="stretch"):
        path = run["audit_path"]
        events = audit_mod.load(path)
        idx = events[len(events) // 2].idx if events else 0
        copy = audit_mod.tamper_copy(path, idx, "payload")
        try:
            tres = audit_mod.verify(copy)
            if tres.ok:
                st.session_state["proof_tamper_result"] = (
                    "fail", "tamper NOT detected — check the audit module")
            else:
                st.session_state["proof_tamper_result"] = (
                    "ok", f"altered event index {idx} in a copy; verifier "
                    f"reports: {tres.reason} (first bad index "
                    f"{tres.first_bad_index}).")
        finally:
            copy.unlink(missing_ok=True)
    tamper = st.session_state.get("proof_tamper_result")
    if tamper is not None:
        (st.success if tamper[0] == "ok" else st.error)(
            tamper[1] + " The tampered copy was deleted after the check.")

    events = [(rec.tick_idx, ev) for rec in records
              for ev in rec.audit_events_new]
    if events:
        rows = [{"tick": t, "event #": ev.idx,
                 "time": ev.ts.strftime("%H:%M"), "kind": ev.kind}
                for t, ev in events[-12:]]
        st.markdown("**Recent audit events**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    head = records[-1].audit_head_hash or ""
    st.caption(f"Audit file: {run['audit_path'].name} · chain head "
               f"{head[:20]}…")


def _expectations_section(ctx: dict) -> None:
    st.markdown("#### 5 · Automatic scenario checks")
    st.caption("The same expectation engine the CLI uses "
               "(scripts/run_scenario.py). Meaningful once the run reaches "
               "the end — press “Run to end” for the full verdict.")
    results = evaluate_expectations(ctx["manifest"], ctx["records"])
    rows = [{"check": r.id, "result": "PASS" if r.passed else "FAIL",
             "detail": r.detail} for r in results]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    n_pass = sum(1 for r in results if r.passed)
    st.caption(f"{n_pass}/{len(results)} checks passing at tick "
               f"{ctx['records'][-1].tick_idx}.")


def render(ctx: dict) -> None:
    if not ctx["records"]:
        st.info("Start a run in the sidebar first.")
        return
    _packet_section(ctx)
    _signature_not_truth(ctx)
    _sequence_section(ctx)
    _audit_section(ctx)
    _expectations_section(ctx)
