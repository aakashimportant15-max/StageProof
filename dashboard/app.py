"""StageProof dashboard — Streamlit shell (DESIGN §6).

Drives the real engine (Runner) exactly like scripts/run_scenario.py: the
dashboard never re-implements decisions. It steps the scenario stream on
demand (Play · Step · Jump), renders stored TickRecord fields only, and
labels every surface as simulated/offline.

Run:  python -m streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
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
            minutes=TICK_MINUTES * int(manifest.warmup_ticks)
        )
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
    columns, dtypes and UTC index as the whole-file read.
