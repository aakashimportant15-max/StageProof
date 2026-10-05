"""Incident & evidence tab (DESIGN §7.2): pick an incident tick and show the
stored evidence bundle behind the verdict.

Display-only: reads TickRecord.evidence / .verdict / .estimate fields exactly
as the engine stored them; the deviation chart uses the configured z
thresholds only to draw reference lines.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import plots

ICON_COLOR = {"ok": "normal", "bad": "attack", "warn": "fault",
              "wait": "uncertain", "unknown": "muted"}
ICON_GLYPH = {"ok": "✓", "bad": "✗", "warn": "!", "wait": "⏳",
              "unknown": "?"}


def _e(x: Any) -> str:
    return x.value if hasattr(x, "value") else ("—" if x is None else str(x))


def _row(label: str, icon: str, detail: str) -> str:
    color = plots.COLORS[ICON_COLOR[icon]]
    return (f"<div style='margin:3px 0'>"
            f"<span style='color:{color};font-weight:700'>"
            f"{ICON_GLYPH[icon]}</span> <b>{label}</b> — "
            f"<span style='color:{plots.COLORS['muted']}'>{detail}</span>"
            f"</div>")


def _checklist(rec: Any) -> str:
    ev = rec.evidence
    if ev is None:
        return "No evidence stored for this tick."
    out: list[str] = []

    flags_b = list((rec.transport_flags or {}).get("B", ()))
    hard_b = list((ev.transport_hard or {}).get("B", ()))
    accepted_b = (rec.accepted or {}).get("B")
    other = {r: list(f) for r, f in (rec.transport_flags or {}).items()
             if r != "B" and f}
    if hard_b or accepted_b is False:
        icon, det = "bad", f"rejected · {', '.join(hard_b or flags_b)}"
    elif flags_b:
        icon, det = "warn", f"soft flags: {', '.join(flags_b)}"
    else:
        icon, det = "ok", "signature valid · station matches channel · seq ok"
    if other:
        det += " · other channels: " + ", ".join(
            f"{r}({', '.join(f)})" for r, f in other.items())
    out.append(_row("Message integrity", icon, det))

    hf = list((ev.health_flags or {}).get("B", ()))
    out.append(_row("Sensor health", "ok" if not hf else "bad",
                    "no health flags" if not hf else ", ".join(hf)))

    if _e(ev.context) == "CONSISTENT":
        icon = "ok"
    elif _e(ev.context) in ("PHANTOM", "SUPPRESSED"):
        icon = "bad"
    else:
        icon = "unknown"
    z = f"{ev.z_mean:+.2f}σ (3-tick mean)" if ev.z_mean is not None else "—"
    out.append(_row("Matches upstream + rainfall", icon,
                    f"{_e(ev.context)} · deviation {z}"))

    trend = _e(ev.up_trend)
    out.append(_row("Upstream trend", {"RISING": "ok", "FLAT": "wait",
                                       "FALLING": "bad"}.get(trend,
                                                             "unknown"),
                    trend.lower()))

    rain = _e(ev.rain)
    out.append(_row("Rainfall support", {"YES": "ok", "NO": "bad"}.get(
        rain, "unknown"), rain.lower()))

    down = _e(ev.downstream)
    zdown = (f" · z_down {ev.z_down_mean:+.1f}σ"
             if ev.z_down_mean is not None else "")
    out.append(_row("Downstream response",
                    {"YES": "ok", "NO": "bad", "PENDING": "wait"}.get(
                        down, "unknown"), down.lower() + zdown))

    if ev.replay_match:
        icon, det = "bad", "readings are an exact copy of an earlier period"
    elif ev.noise_too_clean:
        icon, det = "bad", "signal unnaturally smooth for a real sensor"
    elif ev.drift:
        icon, det = "wait", "slow sustained drift consistent with calibration"
    else:
        icon, det = "ok", "no replay / copy or drift pattern"
    out.append(_row("Replay & realism", icon, det))

    shape_ok = ev.shape_ok
    rate = ev.rate_exceeded
    if shape_ok is False or rate:
        icon, det = "bad", (f"shape {'violated' if shape_ok is False else 'ok'}"
                            f" · rate {'exceeded' if rate else 'ok'}")
    else:
        icon, det = "ok", "shape plausible · rate within limits"
    out.append(_row("Physical shape & rate", icon, det))

    lv = ""
    if ev.obs_stage is not None:
        lv = f"observed {ev.obs_stage:.2f} ft"
    out.append(_row("Notable level", "ok" if ev.notable else "unknown",
                    (lv + " · " if lv else "") +
                    ("near/above a decision level" if ev.notable
                     else "below decision levels")))

    inps = ", ".join(ev.trusted_inputs) or "none"
    out.append(_row("Input quality", "wait" if ev.degraded else "ok",
                    f"model variant {ev.variant} · degraded "
                    f"{'yes' if ev.degraded else 'no'} · trusted: {inps}"))
    return "".join(out)


def _incidents(records) -> list[int]:
    out: list[int] = []
    prev = None
    for rec in records:
        v = rec.verdict
        sig = (v.label.value if v else None, v.subtype if v else None,
               rec.alert_state.value)
        if prev is None or sig != prev or (v is not None and v.changed):
            out.append(rec.tick_idx)
        prev = sig
    return out or [records[-1].tick_idx]


def render(ctx: dict) -> None:
    records = ctx["records"]
    if not records:
        st.info("Start a run in the sidebar first.")
        return
    ticks = _incidents(records)
    labels = {}
    for t in ticks:
        rec = records[t]
        v = rec.verdict
        labels[t] = (f"t{t} · {rec.ts.strftime('%d %b %H:%M')} · "
                     f"{_e(v.label) if v else '—'}"
                     + (f"/{v.subtype}" if v and v.subtype else "")
                     + f" · alert {_e(rec.alert_state)}")
    idx = st.selectbox("Incident tick (verdict / alert changes)",
                       options=list(range(len(ticks))),
                       format_func=lambda i: labels[ticks[i]],
                       index=len(ticks) - 1, key="ev_incident")
    if not isinstance(idx, int) or not 0 <= idx < len(ticks):
        idx = len(ticks) - 1
    rec = records[ticks[idx]]
    ev = rec.evidence
    v = rec.verdict

    c = st.columns(4)
    c[0].metric("Verdict", (_e(v.label) if v else "—")
                + (f" · {v.subtype}" if v and v.subtype else ""))
    c[1].metric("Confidence", _e(v.confidence) if v else "—")
    c[2].metric("Alert", _e(rec.alert_state)
                + (f" · {rec.alert_source}" if rec.alert_source else ""))
    c[3].metric("Sensor B", _e(rec.sensor_status.get("B").state)
                if rec.sensor_status.get("B") else "—")

    c1, c2 = st.columns([1.2, 1])
    with c1:
        st.markdown(f"**Evidence trail — tick {rec.tick_idx} "
                    f"({rec.ts.strftime('%d %b %H:%M')} UTC)**")
        st.markdown(_checklist(rec), unsafe_allow_html=True)
    with c2:
        if v is not None:
            st.markdown(f"**Rule {v.rule_id}** · "
                        f"persist {v.persist_count}/{v.persist_needed}")
            reasons = ctx["settings"].messages.reasons
            for code in v.reasons:
                st.markdown(f"- {reasons.get(code, code)}")
            if v.candidate is not None and _e(v.candidate) != _e(v.label):
                st.caption(f"candidate {_e(v.candidate)}"
                           + (f"/{v.candidate_subtype}"
                              if v.candidate_subtype else "")
                           + " not yet committed")
        if ev is not None and ev.obs_stage is not None \
                and ev.pred_stage is not None:
            st.markdown(f"**Observed vs expected**")
            st.markdown(f"- observed **{ev.obs_stage:.2f} ft** · "
                        f"expected **{ev.pred_stage:.2f} ft**"
                        + (f" → **{ev.z_mean:+.2f}σ**" if ev.z_mean
                           is not None else ""))
        est = rec.estimate
        if est is not None and est.in_use:
            st.markdown(f"**Fallback estimate in use**")
            st.markdown(
                f"- stage **{est.stage_hat:.2f} ft** "
                f"[{est.stage_lo:.2f}, {est.stage_hi:.2f}] · "
                f"{_e(est.confidence)} confidence\n"
                f"- basis: {est.basis} · variant {est.variant} · "
                f"q_hat {est.q_hat:.0f}")
            st.caption("B is quarantined; warnings on this basis are "
                       "labelled as estimates.")

    zc = float(ctx["settings"].threshold("context", "z_consistent"))
    zi = float(ctx["settings"].threshold("context", "z_implausible"))
    st.plotly_chart(plots.deviation_figure(records, zc, zi), theme=None,
                    width="stretch")
    st.caption("Signed deviation of stored observations from the stored "
               f"expectation. ±{zc:g}σ = consistent band, ±{zi:g}σ = "
               "implausible (RULES §5). ▲ = committed verdict changes.")

    if rec.new_actions:
        acts = " · ".join(f"{a.type.value}→{a.target or '—'} "
                          f"[{a.status.value}]" for a in rec.new_actions)
        st.markdown(f"**Actions this tick:** {acts}")
    kinds = [e.kind for e in rec.audit_events_new]
    st.caption(f"Audit events this tick: {', '.join(kinds) or 'none'} · "
               f"halted={rec.halted_audit} · chain head "
               f"{(rec.audit_head_hash or '')[:16]}…")
