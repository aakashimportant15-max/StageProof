"""Live operations tab (DESIGN §6.2): what StageProof sees and decides now.

Display-only: every value is read from stored TickRecord fields produced by
the real engine (dashboard/app.py drives Runner exactly like
scripts/run_scenario.py). Nothing is recomputed here; the expected band in
the chart is the only derived quantity and it comes from the stored
prediction (plots.main_chart).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

import plots

from stageproof.domain import AlertState

REPLY_MEANING = {1: "water is above the red mark",
                 2: "water is not above the mark",
                 3: "not sure"}
TIER_CHIP = {0: "0 · auto", 1: "1 · auto (demo policy)",
             2: "2 · HUMAN ONLY"}
ROUND_COLOR = {"OPEN": "uncertain", "CONFIRMED": "normal",
               "REFUTED": "attack", "TIMEOUT": "fault"}


def _e(x: Any) -> str:
    return x.value if hasattr(x, "value") else ("—" if x is None else str(x))


def _tri(x: Any) -> str:
    return "?" if x is None else ("yes" if x else "no")


def _kpi(col, title: str, value: str, color: str | None = None,
         sub: str | None = None) -> None:
    color = color or plots.COLORS["text"]
    sub_html = (f"<div style='color:{plots.COLORS['muted']};"
                f"font-size:0.68rem;margin-top:2px'>{sub}</div>"
                if sub else "")
    col.markdown(
        f"<div style='background:{plots.COLORS['panel']};"
        f"border:1px solid {plots.COLORS['border']};border-radius:6px;"
        f"padding:6px 10px;min-height:58px'>"
        f"<div style='color:{plots.COLORS['muted']};font-size:0.66rem;"
        f"text-transform:uppercase;letter-spacing:.05em'>{title}</div>"
        f"<div style='color:{color};font-weight:600;font-size:0.9rem;"
        f"line-height:1.25'>{value}</div>{sub_html}</div>",
        unsafe_allow_html=True)


def _panel(title: str, body_html: str) -> None:
    st.markdown(
        f"<div style='background:{plots.COLORS['panel']};"
        f"border:1px solid {plots.COLORS['border']};border-radius:6px;"
        f"padding:10px 12px;margin-bottom:4px'>"
        f"<div style='color:{plots.COLORS['muted']};font-size:0.68rem;"
        f"text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px'>"
        f"{title}</div>{body_html}</div>", unsafe_allow_html=True)


def _kpi_row(ctx: dict, last: Any) -> None:
    v = last.verdict
    ev = last.evidence
    est = last.estimate
    stb = last.sensor_status.get("B")
    cols = st.columns([1.7, 1.5, 1.3, 1.8, 1.5])

    label = _e(v.label) if v else "—"
    _kpi(cols[0], "Verdict",
         label + (f" · {v.subtype}" if v and v.subtype else ""),
         color=plots.VERDICT_COLORS.get(_e(v.label) if v else ""),
         sub=(f"{_e(v.confidence)} confidence · rule {v.rule_id} · "
              f"persist {v.persist_count}/{v.persist_needed}"
              if v else "no verdict yet"))

    src = f" · {last.alert_source}" if last.alert_source else ""
    _kpi(cols[1], "Alert state", _e(last.alert_state) + src,
         color=(plots.COLORS["attack"]
                if _e(last.alert_state) in ("PROVISIONAL_WARNING",
                                            "CONFIRMED_WARNING")
                else None),
         sub="no public warning" if _e(last.alert_state) == "NONE" else None)

    _kpi(cols[2], "Sensor B trust", _e(stb.state) if stb else "—",
         color=(plots.COLORS.get("quarantine")
                if stb and _e(stb.state) == "QUARANTINED" else None),
         sub=(f"{stb.reason or 'no flags'} · clean streak {stb.clean_streak}"
              if stb else None))

    obs = ev.obs_stage if ev else None
    pred = ev.pred_stage if ev else None
    if obs is not None and pred is not None:
        val, sub = f"{obs:.2f} / {pred:.2f} ft", (
            f"z_mean {ev.z_mean:+.1f}σ · context {_e(ev.context)}"
            if ev.z_mean is not None else "observed / expected")
    elif est is not None and est.in_use:
        val, sub = f"— / est {est.stage_hat:.2f} ft", "B untrusted"
    else:
        val, sub = "— / —", "no trusted reading"
    _kpi(cols[3], "B observed / expected", val, sub=sub)

    lv = f"{last.best_level:.2f} ft" if last.best_level is not None else "—"
    _kpi(cols[4], "Level vs thresholds", lv,
         sub=(f"source {last.best_source or '—'} · watch "
              f"{ctx['levels'].get('watch_stage', '?')} / action "
              f"{ctx['levels'].get('action_stage', '?')}"))

    if est is not None and est.in_use:
        st.markdown(
            f"<div style='border-left:4px solid {plots.COLORS['uncertain']};"
            f"background:rgba(204,121,167,0.10);border-radius:6px;"
            f"padding:8px 12px;margin:6px 0'>"
            f"<span style='color:{plots.COLORS['uncertain']};font-weight:700'>"
            f"ESTIMATE IN USE</span> — sensor B is quarantined; the engine's "
            f"level and warning basis come from the fallback estimate "
            f"{est.stage_hat:.2f} ft [{est.stage_lo:.2f}, {est.stage_hi:.2f}] "
            f"· {_e(est.confidence)} confidence · basis: {est.basis}.</div>",
            unsafe_allow_html=True)


def _why_panel(ctx: dict, last: Any) -> None:
    v, ev = last.verdict, last.evidence
    reasons = ctx["settings"].messages.reasons
    parts: list[str] = []
    if v is not None:
        if v.candidate is not None and _e(v.candidate) != _e(v.label):
            parts.append(
                f"<b>Candidate</b> {_e(v.candidate)}"
                + (f" / {v.candidate_subtype}" if v.candidate_subtype else "")
                + f" — holding {v.persist_count}/{v.persist_needed} ticks "
                f"before commit.")
        if v.changed:
            parts.append("<b>Committed this tick.</b>")
        for code in v.reasons:
            parts.append(f"• {reasons.get(code, code)} "
                         f"<span style='color:{plots.COLORS['muted']}'>"
                         f"({code})</span>")
    if ev is not None:
        parts.append(
            f"<span style='color:{plots.COLORS['muted']}'>B signature: "
            f"shape {_tri(ev.shape_ok)} · rate "
            f"{'exceeded' if ev.rate_exceeded else 'ok'} · "
            f"replay match {_tri(ev.replay_match)} · too-clean "
            f"{_tri(ev.noise_too_clean)} · drift {_tri(ev.drift)} · "
            f"model variant {ev.variant} · trusted inputs: "
            f"{', '.join(ev.trusted_inputs) or '—'}</span>")
    if last.new_actions:
        acts = "; ".join(f"{a.type.value} → {a.target or '—'} "
                         f"[{a.status.value}]" for a in last.new_actions)
        parts.append(f"<b>Result:</b> {acts}")
    else:
        parts.append("<b>Result:</b> no new actions this tick.")
    _panel("Why this verdict", "<br>".join(parts) or "—")


def _round_card(ctx: dict, last: Any) -> None:
    ver = last.verification
    if not ver:
        return
    status = str(ver.get("status", "?"))
    color = plots.COLORS[ROUND_COLOR.get(status, "muted")]
    replies = ver.get("replies") or {}
    rep_txt = ", ".join(f"{vid} → {REPLY_MEANING.get(int(c), c)}"
                        for vid, c in replies.items()) or "none yet"
    post = ver.get("posterior")
    _panel("Community verification round " + str(ver.get("round_id", "")),
           f"<span style='color:{color};font-weight:700'>{status}</span> · "
           f"opened tick {ver.get('opened_tick', '?')} · informative replies "
           f"{ver.get('informative', 0)}"
           + (f" · posterior P(water above mark) {float(post):.0%}"
              if post is not None else "")
           + f"<br><span style='color:{plots.COLORS['muted']}'>replies: "
             f"{rep_txt}</span>")


def _sensors_panel(ctx: dict, last: Any) -> None:
    lines: list[str] = []
    for role in ("A", "T", "B", "C"):
        stt = last.sensor_status.get(role)
        reading = (last.readings or {}).get(role)
        accepted = (last.accepted or {}).get(role)
        flags = list((last.transport_flags or {}).get(role, ()))
        state = _e(stt.state) if stt else "—"
        color = {"TRUSTED": plots.COLORS["normal"],
                 "SUSPECT": plots.COLORS["fault"],
                 "QUARANTINED": plots.COLORS["quarantine"],
                 "RECOVERING": plots.COLORS["uncertain"]}.get(
                     state, plots.COLORS["muted"])
        mark = "—" if accepted is None else ("✓ accepted" if accepted
                                             else "✗ rejected")
        flag_txt = (" · flags: " + ", ".join(flags)) if flags else ""
        reason = (f" · {stt.reason}" if stt and stt.reason else "")
        lines.append(
            f"<span style='color:{color}'>●</span> <b>{role}</b> {state} · "
            f"seq {reading.seq if reading else '—'} · {mark}"
            f"<span style='color:{plots.COLORS['muted']}'>{reason}"
            f"{flag_txt}</span>")
    _panel("Sensor trust",
           "<br>".join(lines)
           + f"<br><span style='color:{plots.COLORS['muted']};"
             f"font-size:0.75rem'>Model inputs: upstream A + tributary T + "
             f"rainfall → expected B; C is the downstream consistency "
             f"check.</span>")


def _baselines_strip(last: Any) -> None:
    b = last.baselines
    if not b:
        return
    cols = st.columns(3)
    _kpi(cols[0], "Baseline: threshold rule",
         "alert raised" if b.get("threshold_alert") else "no alert")
    _kpi(cols[1], "Baseline: rolling z",
         f"{b.get('rollz_value'):+.2f}σ" if b.get("rollz_value") is not None
         else "—")
    _kpi(cols[2], "Baseline: z flag",
         "raised" if b.get("rollz_flag") else "—")
    st.caption("Legacy baselines shown for comparison only — StageProof's "
               "verdict comes from the evidence checks above, not from "
               "these thresholds.")


def _action_timeline(records) -> None:
    rows = []
    for rec in records:
        for a in rec.new_actions:
            rows.append({
                "tick": rec.tick_idx,
                "time": a.ts.strftime("%H:%M"),
                "action": a.type.value,
                "target": a.target or "—",
                "tier": TIER_CHIP.get(a.tier, f"{a.tier}"),
                "status": a.status.value,
                "by": a.approver or a.source or "engine",
            })
    rows = rows[-8:]
    st.markdown("**Action log (latest 8)**")
    if not rows:
        st.caption("No actions yet.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(len(rows), 8) * 35 + 38)


def _phone_thread(records) -> None:
    msgs = [(rec.tick_idx, m) for rec in records for m in rec.new_messages]
    phone = [(t, m) for t, m in msgs if m.channel in ("SMS", "IVR")]
    if not phone:
        st.caption("No SMS/IVR messages yet — keep playing.")
        return
    items = []
    for tick, m in phone[-10:]:
        items.append(
            f"<div style='margin-bottom:6px'>"
            f"<span style='color:{plots.COLORS['muted']}'>t{tick} "
            f"{m.ts.strftime('%H:%M')} · {m.channel} → {m.audience} "
            f"[{m.language}]"
            + (f" · to {m.recipient_id}" if m.recipient_id else "")
            + f"</span><br>{m.text}</div>")
    _panel("Phone simulator — DEMO (nothing leaves this machine)",
           "".join(items))


def _officer_console(ctx: dict, last: Any) -> None:
    run = ctx["run"]
    finished = run["finished"] or run["error"] is not None
    pending = run["runner"].pending_actions()
    if pending:
        st.markdown("**Pending approvals (tier 2 waits for a human)**")
        for a in pending:
            if finished:
                st.markdown(f"`{a.action_id}` {a.type.value} → "
                            f"{a.target or '—'} · still pending (run ended)")
                continue
            c1, c2, c3 = st.columns([3.2, 1, 1])
            c1.markdown(f"`{a.action_id}` {a.type.value} → {a.target or '—'}")
            if c2.button("Approve", key=f"ap_{a.action_id}",
                         width="stretch"):
                ctx["controls"]["approve"](a.action_id)
            if c3.button("Reject", key=f"rj_{a.action_id}",
                         width="stretch"):
                ctx["controls"]["reject"](a.action_id)
    stb = last.sensor_status.get("B")
    if stb is not None and stb.needs_ack:
        st.warning("Sensor B is quarantined and needs an explicit "
                   "acknowledgement to start recovery.")
        if finished:
            st.caption("Run ended — the acknowledgement is a live operator "
                       "action; press Start / restart to replay it.")
        elif st.button("Acknowledge & release sensor B (tier 2)",
                       key="ack_release", width="stretch"):
            ctx["controls"]["ack_release"]()
    state = last.alert_state
    if finished:
        if state in (AlertState.PROVISIONAL_WARNING,
                     AlertState.CONFIRMED_WARNING):
            st.caption("Run ended — officer confirm / clear are live "
                       "operator actions; press Start / restart to replay.")
    elif state == AlertState.PROVISIONAL_WARNING:
        if st.button("Officer: confirm the provisional warning",
                     key="off_confirm", width="stretch"):
            ctx["controls"]["officer_confirm"]()
    if not finished and state in (AlertState.PROVISIONAL_WARNING,
                                  AlertState.CONFIRMED_WARNING):
        if st.button("Officer: clear the alert", key="off_clear",
                     width="stretch"):
            ctx["controls"]["officer_clear"]()
    officer_msgs = [m for rec in ctx["records"] for m in rec.new_messages
                    if m.channel in ("OFFICER", "TICKET")]
    if officer_msgs:
        with st.expander(f"Officer / ticket messages ({len(officer_msgs)})",
                         expanded=False):
            for m in officer_msgs[-8:]:
                st.markdown(f"`{m.ts.strftime('%H:%M')} · {m.channel}` "
                            f"{m.text}")
    st.caption("Officer actions apply at the next tick (operator events, "
               "same as the CLI autopilot).")


def _reply_buttons(ctx: dict) -> None:
    run = ctx["run"]
    ver = run["records"][-1].verification if run["records"] else None
    if ctx["autopilot"] or not run["pending_replies"]:
        return
    if not ver or ver.get("status") != "OPEN":
        return
    st.markdown("**Volunteer replies (you are the phone simulator)**")
    vols = ctx["settings"].messages.volunteers
    for i, vrep in enumerate(list(run["pending_replies"])):
        vid = str(vrep["volunteer_id"])
        code = int(vrep["code"])
        name = vols.get(vid, {}).get("name", vid)
        label = f"📱 {name} replies: “{REPLY_MEANING.get(code, code)}”"
        if st.button(label, key=f"vrep_{i}_{vid}_{code}", width="stretch"):
            ok, detail = ctx["controls"]["submit_reply"](vrep)
            st.session_state["live_flash"] = (
                "ok" if ok else "err", f"{name}: {detail}")
            st.rerun()


def render(ctx: dict) -> None:
    records = ctx["records"]
    if not records:
        st.info("Press **Start / restart run** in the sidebar to replay a "
                "scenario through the engine.")
        return
    last = records[-1]
    flash = st.session_state.pop("live_flash", None)
    if flash:
        (st.success if flash[0] == "ok" else st.error)(flash[1])

    _kpi_row(ctx, last)

    fig = plots.main_chart(records, ctx["rain"], ctx["bundle"], ctx["levels"],
                           ctx["cursor"], ctx["band_z"],
                           show_truth=ctx["reveal_truth"],
                           injected=ctx["injected"])
    st.plotly_chart(fig, theme=None, width="stretch")
    st.caption("Dotted line = stored model expectation for B; shaded band = "
               f"prediction ± {ctx['band_z']:g}σ converted through the "
               "model's rating curve. Grey vertical spans = B quarantined. "
               "The engine's verdict logic reads none of this — it uses the "
               "stored numbers shown here.")

    c1, c2 = st.columns([1.15, 1])
    with c1:
        _why_panel(ctx, last)
        _round_card(ctx, last)
    with c2:
        _sensors_panel(ctx, last)
        _baselines_strip(last)

    c3, c4 = st.columns([1.15, 1])
    with c3:
        _phone_thread(records)
        _reply_buttons(ctx)
    with c4:
        _officer_console(ctx, last)

    _action_timeline(records)
    head = last.audit_head_hash or ""
    st.caption(f"Audit head {head[:16]}… · {len(last.audit_events_new)} new "
               f"audit events this tick — full chain in the Proof & audit "
               f"tab.")
