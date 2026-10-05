"""Plotly figure builders for the StageProof dashboard (DESIGN §5–§6.3, §8.2-C).

Display-only: every series is read from stored TickRecord fields (engine
output) or from the same scenario stream the Runner consumed. No verdict,
alert or decision logic is recomputed here. The one derived quantity is the
expected band: `prediction.logq_pred ± band_z * prediction.scale` converted
through the model's own rating curve, where `band_z` is the engine config
value (thresholds.context.band_z) and the rating is the one the engine used.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional, Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# DESIGN §5.1 palette
COLORS = {
    "bg": "#0E1117",
    "panel": "#161B22",
    "border": "#2A313C",
    "text": "#E6EDF3",
    "muted": "#8B98A5",
    "observed": "#E6EDF3",
    "expected": "#56B4E9",
    "estimate": "#C9D1D9",
    "quarantine": "#6E7681",
    "real": "#56B4E9",
    "fault": "#E69F00",
    "attack": "#FF5C5C",
    "uncertain": "#CC79A7",
    "normal": "#009E73",
}

VERDICT_COLORS = {
    "NORMAL": COLORS["normal"],
    "REAL_FLOOD": COLORS["real"],
    "SENSOR_FAULT": COLORS["fault"],
    "POSSIBLE_CYBER_ATTACK": COLORS["attack"],
    "UNCERTAIN": COLORS["uncertain"],
}

# DESIGN §5.2 line/marker styles per verdict
VERDICT_DASH = {
    "REAL_FLOOD": "solid",
    "SENSOR_FAULT": "dash",
    "POSSIBLE_CYBER_ATTACK": "dot",
    "UNCERTAIN": "dashdot",
}
VERDICT_SYMBOL = {
    "SENSOR_FAULT": "x",
    "POSSIBLE_CYBER_ATTACK": "triangle-up",
    "UNCERTAIN": "diamond",
}

_PLOT_BG = dict(
    paper_bgcolor=COLORS["bg"],
    plot_bgcolor=COLORS["panel"],
    font=dict(color=COLORS["text"], size=12),
    margin=dict(l=60, r=20, t=30, b=30),
)
_AXIS = dict(
    gridcolor=COLORS["border"],
    zerolinecolor=COLORS["border"],
    linecolor=COLORS["border"],
    tickfont=dict(color=COLORS["muted"]),
)


def expected_band(bundle: Any, role: str, logq_pred: Optional[float],
                  scale: Optional[float], band_z: float
                  ) -> tuple[Optional[float], Optional[float]]:
    """Convert the stored prediction interval to stage bounds (display only)."""
    if logq_pred is None or scale is None:
        return None, None
    rating = getattr(bundle, "ratings", {}).get(role)
    if rating is None:
        return None, None
    try:
        lo_q = math.exp(float(logq_pred) - band_z * float(scale))
        hi_q = math.exp(float(logq_pred) + band_z * float(scale))
        return (float(rating.stage_from_q(lo_q)),
                float(rating.stage_from_q(hi_q)))
    except (ValueError, OverflowError):
        return None, None


def _ts(records: Sequence[Any]) -> list[datetime]:
    return [rec.ts for rec in records]


def _stage_series(records: Sequence[Any], role: str) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for rec in records:
        reading = (rec.readings or {}).get(role)
        out.append(float(reading.stage) if reading is not None else None)
    return out


def quarantine_spans(records: Sequence[Any], role: str) -> list[tuple[Any, Any]]:
    """Contiguous (start_ts, end_ts) spans where a role is quarantined."""
    spans: list[tuple[Any, Any]] = []
    start: Any = None
    prev_ts: Any = None
    for rec in records:
        st = rec.sensor_status.get(role)
        q = st is not None and st.state.value == "QUARANTINED"
        if q and start is None:
            start = rec.ts
        if not q and start is not None:
            spans.append((start, prev_ts))
            start = None
        prev_ts = rec.ts
    if start is not None:
        spans.append((start, prev_ts))
    return spans


def _verdict_runs(records: Sequence[Any]) -> list[tuple[int, int, Any]]:
    """Maximal [i0, i1, (label, subtype)] runs of committed verdicts."""
    runs: list[tuple[int, int, Any]] = []
    cur: Optional[list] = None
    for i, rec in enumerate(records):
        v = rec.verdict
        key = (v.label.value, v.subtype) if v is not None else None
        if cur is not None and cur[2] == key:
            cur[1] = i
        else:
            if cur is not None:
                runs.append((cur[0], cur[1], cur[2]))
            cur = [i, i, key]
    if cur is not None:
        runs.append((cur[0], cur[1], cur[2]))
    return runs


def main_chart(records: Sequence[Any], rain_prev_hr: Sequence[Optional[float]],
               bundle: Any, levels: dict, cursor: int, band_z: float,
               show_truth: bool = False,
               injected: Sequence[dict] = ()) -> go.Figure:
    """Four stacked panels with a shared time axis (DESIGN §6.3)."""
    xs = _ts(records)

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.035,
        row_heights=[0.16, 0.20, 0.40, 0.24],
        subplot_titles=(
            "① Rainfall (previous hour, mm)",
            "② Upstream A and tributary T (observed)",
            "③ Target B: observed · expected · estimate",
            "④ Downstream C (observed · expected)",
        ),
    )

    # ① rainfall
    fig.add_trace(go.Bar(
        x=xs, y=list(rain_prev_hr), name="Rainfall",
        marker_color="rgba(86,180,233,0.55)", hoverinfo="x+y"
    ), row=1, col=1)

    # ② upstream A and T
    for role, color in (("A", COLORS["expected"]), ("T", COLORS["muted"])):
        fig.add_trace(go.Scatter(
            x=xs, y=_stage_series(records, role), name=f"{role} (feed)",
            mode="lines", line=dict(color=color, width=1.6),
            connectgaps=False,
        ), row=2, col=1)

    # ③ target B
    watch = levels.get("watch_stage")
    action = levels.get("action_stage")
    if watch is not None:
        fig.add_hline(y=float(watch), line=dict(color=COLORS["muted"],
                      width=1, dash="dash"), row=3, col=1,
                      annotation_text=f"watch {float(watch):.1f}",
                      annotation_font_color=COLORS["muted"])
    if action is not None:
        fig.add_hline(y=float(action), line=dict(color=COLORS["fault"],
                      width=1.2, dash="dash"), row=3, col=1,
                      annotation_text=f"action {float(action):.1f}",
                      annotation_font_color=COLORS["fault"])

    for x0, x1 in quarantine_spans(records, "B"):
        fig.add_vrect(x0=x0, x1=x1, row=3, col=1, layer="below",
                      fillcolor="rgba(110,118,129,0.25)",
                      line_width=0)

    obs_b = _stage_series(records, "B")
    fig.add_trace(go.Scatter(
        x=xs, y=obs_b, name="OBSERVED (sensor B)", mode="lines",
        line=dict(color=COLORS["observed"], width=1.8), connectgaps=False,
    ), row=3, col=1)

    # verdict-colored overlays on B (styles per DESIGN §5.2)
    for i0, i1, key in _verdict_runs(records):
        if key is None or key[0] == "NORMAL":
            continue
        label = key[0]
        fig.add_trace(go.Scatter(
            x=xs[i0:i1 + 1], y=obs_b[i0:i1 + 1],
            mode="lines+markers" if label in VERDICT_SYMBOL else "lines",
            name=f"{label}" + (f" · {key[1]}" if key[1] else ""),
            line=dict(color=VERDICT_COLORS[label],
                      width=2.6, dash=VERDICT_DASH.get(label, "solid")),
            marker=dict(symbol=VERDICT_SYMBOL.get(label, "circle"),
                        size=6, color=VERDICT_COLORS[label]),
            showlegend=False, connectgaps=False,
            hoverinfo="skip" if label not in VERDICT_SYMBOL else "x+y",
        ), row=3, col=1)

    # expected band + line from stored prediction (B)
    exp_y: list[Optional[float]] = []
    exp_lo: list[Optional[float]] = []
    exp_hi: list[Optional[float]] = []
    for rec in records:
        pred = rec.prediction
        if pred is None or pred.stage_pred is None:
            exp_y.append(None); exp_lo.append(None); exp_hi.append(None)
            continue
        lo, hi = expected_band(bundle, "B", pred.logq_pred, pred.scale, band_z)
        exp_y.append(float(pred.stage_pred))
        exp_lo.append(lo); exp_hi.append(hi)
    fig.add_trace(go.Scatter(x=xs, y=exp_hi, mode="lines", showlegend=False,
                             line=dict(width=0), hoverinfo="skip"),
                  row=3, col=1)
    fig.add_trace(go.Scatter(
        x=xs, y=exp_lo, mode="lines", name="EXPECTED from upstream + rain",
        line=dict(width=0), fill="tonexty",
        fillcolor="rgba(86,180,233,0.25)", hoverinfo="skip"),
        row=3, col=1)
    fig.add_trace(go.Scatter(
        x=xs, y=exp_y, mode="lines", name="expected (stored)",
        line=dict(color=COLORS["expected"], width=1.2, dash="dot"),
        connectgaps=False), row=3, col=1)

    # estimate (dotted, band when in use)
    est_y: list[Optional[float]] = []
    est_lo: list[Optional[float]] = []
    est_hi: list[Optional[float]] = []
    for rec in records:
        est = rec.estimate
        if est is not None and est.in_use and est.stage_hat is not None:
            est_y.append(float(est.stage_hat))
            est_lo.append(est.stage_lo)
            est_hi.append(est.stage_hi)
        else:
            est_y.append(None); est_lo.append(None); est_hi.append(None)
    fig.add_trace(go.Scatter(x=xs, y=est_hi, mode="lines", showlegend=False,
                             line=dict(width=0), hoverinfo="skip"),
                  row=3, col=1)
    fig.add_trace(go.Scatter(
        x=xs, y=est_lo, mode="lines", name="estimate interval",
        line=dict(width=0), fill="tonexty",
        fillcolor="rgba(201,209,217,0.18)", hoverinfo="skip"),
        row=3, col=1)
    fig.add_trace(go.Scatter(
        x=xs, y=est_y, mode="lines", name="ESTIMATED — B quarantined",
        line=dict(color=COLORS["estimate"], width=1.8, dash="dot"),
        connectgaps=False), row=3, col=1)

    # rejected packets (✖ on the observed axis)
    for role in ("A", "T", "B", "C"):
        rxs, rys, rtext = [], [], []
        for rec in records:
            reading = (rec.readings or {}).get(role)
            accepted = (rec.accepted or {}).get(role, True)
            if reading is not None and not accepted:
                flags = ", ".join((rec.transport_flags or {}).get(role, ()))
                rxs.append(rec.ts); rys.append(float(reading.stage))
                rtext.append(f"rejected: {flags}")
        if rxs:
            fig.add_trace(go.Scatter(
                x=rxs, y=rys, mode="markers", name="rejected packet",
                marker=dict(symbol="x", size=9, color=COLORS["attack"]),
                text=rtext, hovertemplate="%{text}<extra></extra>",
                showlegend=False), row=3, col=1)

    # ④ downstream C
    fig.add_trace(go.Scatter(
        x=xs, y=_stage_series(records, "C"), name="C observed",
        mode="lines", line=dict(color=COLORS["observed"], width=1.6),
        connectgaps=False), row=4, col=1)
    c_pred = [float(rec.downstream_prediction.stage_pred)
              if rec.downstream_prediction is not None
              and rec.downstream_prediction.stage_pred is not None else None
              for rec in records]
    fig.add_trace(go.Scatter(
        x=xs, y=c_pred, name="C expected (from B lag)",
        mode="lines", line=dict(color=COLORS["expected"], width=1, dash="dot"),
        connectgaps=False), row=4, col=1)

    # ground truth overlay (never shown by default)
    if show_truth:
        truth_y = [float(rec.truth.true_stage["B"])
                   if rec.truth is not None
                   and rec.truth.true_stage.get("B") is not None else None
                   for rec in records]
        fig.add_trace(go.Scatter(
            x=xs, y=truth_y, name="GROUND TRUTH (not visible to StageProof)",
            mode="lines",
            line=dict(color="rgba(0,158,115,0.55)", width=1.4, dash="dot"),
            connectgaps=False), row=3, col=1)
        for entry in injected:
            i0 = int(entry.get("start_tick", 0))
            i1 = min(int(entry.get("end_tick", 0)), len(records) - 1)
            if not 0 <= i0 < len(records) or i1 < i0:
                continue
            ctype = str(entry.get("type", "INJECTED"))
            fig.add_vrect(x0=xs[i0], x1=xs[i1], row=3, col=1, layer="below",
                          fillcolor="rgba(0,158,115,0.07)", line_width=0,
                          annotation_text=f"GROUND TRUTH · INJECTED: {ctype}",
                          annotation_position="top left",
                          annotation_font_color="rgba(0,158,115,0.8)",
                          annotation_font_size=10)

    # cursor at the current tick
    ci = max(0, min(int(cursor), len(records) - 1))
    fig.add_vline(x=xs[ci], line=dict(color=COLORS["text"], width=1,
                                      dash="dot"))

    fig.update_layout(
        height=620, barmode="overlay",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0, font=dict(size=11)),
        hovermode="x unified",
        **_PLOT_BG,
    )
    for r in range(1, 5):
        fig.update_xaxes(**_AXIS, row=r, col=1)
        fig.update_yaxes(**_AXIS, row=r, col=1)
    fig.update_yaxes(title_text="mm", row=1, col=1)
    fig.update_yaxes(title_text="ft", row=2, col=1)
    fig.update_yaxes(title_text="ft", row=3, col=1)
    fig.update_yaxes(title_text="ft", row=4, col=1)
    return fig


def seq_figure(records: Sequence[Any]) -> go.Figure:
    """Per-channel sequence vs tick with replay/backward points highlighted
    (DESIGN §8.2-C, Proof screen)."""
    xs = _ts(records)
    fig = go.Figure()
    role_colors = {"A": COLORS["expected"], "T": COLORS["muted"],
                   "B": COLORS["observed"], "C": COLORS["normal"]}
    for role in ("A", "T", "B", "C"):
        ys = [int(rec.readings[role].seq)
              if (rec.readings or {}).get(role) is not None else None
              for rec in records]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, name=f"{role} seq", mode="lines+markers",
            marker=dict(size=4), line=dict(color=role_colors[role], width=1.4),
            connectgaps=False))
        bad_x, bad_y, bad_t = [], [], []
        prev: Optional[int] = None
        for rec in records:
            reading = (rec.readings or {}).get(role)
            if reading is None:
                continue
            seq = int(reading.seq)
            flags = list((rec.transport_flags or {}).get(role, ()))
            if seq <= (prev if prev is not None else 0) or any(
                    "SEQ" in f for f in flags):
                bad_x.append(rec.ts); bad_y.append(seq)
                bad_t.append("replay/backward: seq %d" % seq)
            prev = seq
        if bad_x:
            fig.add_trace(go.Scatter(
                x=bad_x, y=bad_y, mode="markers", name=f"{role} replay/dup",
                marker=dict(symbol="x", size=11, color=COLORS["attack"]),
                text=bad_t, hovertemplate="%{text}<extra></extra>"))
    fig.update_layout(height=280, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, font=dict(size=11)),
                      **_PLOT_BG)
    fig.update_xaxes(**_AXIS)
    fig.update_yaxes(**_AXIS, title_text="sequence number")
    return fig


def deviation_figure(records: Sequence[Any], z_consistent: float,
                     z_implausible: float) -> go.Figure:
    """Signed mean deviation z_mean with thresholds and committed markers
    (DESIGN §7.2 deviation timeline)."""
    xs = _ts(records)
    z = [rec.evidence.z_mean if rec.evidence is not None else None
         for rec in records]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=z, name="deviation from expected (σ, 3-tick mean)",
        mode="lines", line=dict(color=COLORS["expected"], width=1.6),
        connectgaps=False))
    for val, color, label in ((z_implausible, COLORS["attack"], "implausible"),
                              (-z_implausible, COLORS["attack"], None),
                              (z_consistent, COLORS["muted"], "consistent"),
                              (-z_consistent, COLORS["muted"], None)):
        fig.add_hline(y=val, line=dict(color=color, width=1, dash="dash"),
                      annotation_text=f"±{val:g} {label}" if label else None,
                      annotation_font_color=color,
                      annotation_font_size=10)
    cx, cy, ct = [], [], []
    for rec in records:
        v = rec.verdict
        if v is not None and v.changed and rec.evidence is not None \
                and rec.evidence.z_mean is not None:
            cx.append(rec.ts); cy.append(rec.evidence.z_mean)
            ct.append(f"committed {v.label.value}"
                      + (f" · {v.subtype}" if v.subtype else "")
                      + f" @ tick {rec.tick_idx}")
    if cx:
        fig.add_trace(go.Scatter(
            x=cx, y=cy, mode="markers", name="committed verdict",
            marker=dict(symbol="triangle-up", size=10, color=COLORS["attack"]),
            text=ct, hovertemplate="%{text}<extra></extra>"))
    fig.update_layout(height=280, hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, font=dict(size=11)),
                      **_PLOT_BG)
    fig.update_xaxes(**_AXIS)
    fig.update_yaxes(**_AXIS, title_text="σ from expected")
    return fig
