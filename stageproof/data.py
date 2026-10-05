"""Dataset and model-artifact loaders (ARCHITECTURE.md §3 L1, §6.8, §13.1, §15).

Runtime access to `data/reach.csv` and `artifacts/model.json`. Every failure
mode raises `ArtifactError` with an actionable message (ARCHITECTURE §14:
scripts exit 2 on artifact/config problems). `load_dataset` enforces the
§13.1 contract: UTC ISO-8601 stamps on a regular 15-minute monotonic grid,
columns `A_stage, A_q, T_stage, T_q, B_stage, B_q, C_stage, C_q,
rain_mm_prev_hr`, blank = missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

__all__ = ["ArtifactError", "load_dataset", "load_model_artifact", "slice_window"]

TICK_MINUTES = 15

#: ARCHITECTURE §13.1 — exact columns of data/reach.csv in order.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "ts_utc",
    "A_stage", "A_q",
    "T_stage", "T_q",
    "B_stage", "B_q",
    "C_stage", "C_q",
    "rain_mm_prev_hr",
)

#: ARCHITECTURE §6.8 — required top-level keys of artifacts/model.json.
MODEL_REQUIRED_KEYS: tuple[str, ...] = (
    "model_version", "created_utc", "data_hash", "config_hash",
    "tick_minutes", "required_history_ticks",
    "ratings", "lags", "variants", "downstream",
    "station_params", "rain", "baseline_stats", "lag_BC_ticks",
)

#: ARCHITECTURE §6.8 — required keys of each entry in `variants`.
VARIANT_REQUIRED_KEYS: tuple[str, ...] = (
    "name", "inputs", "lags", "feature_names", "mean", "std",
    "coef", "intercept", "alpha", "scale_normal", "scale_high",
    "high_flow_q_threshold",
)


class ArtifactError(Exception):
    """Missing or corrupted dataset/model artifact (ARCHITECTURE §14)."""


def _as_utc_timestamp(value, what: str) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ArtifactError(f"{what} is not a valid timestamp: {value!r} ({exc})") from exc
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------

def load_dataset(path: str | Path) -> pd.DataFrame:
    """Load and validate `data/reach.csv`; returns a DataFrame indexed by a
    tz-aware UTC DatetimeIndex named `ts_utc` (the string column is replaced
    by the index). Raises ArtifactError with an actionable message on any
    violation of the ARCHITECTURE §13.1 contract."""
    path = Path(path)
    if not path.exists():
        raise ArtifactError(
            f"dataset not found: {path} — run "
            f"'python scripts/prepare_data.py' to download and build it "
            f"(the only network-using script; TASKS T007/T008)"
        )
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ArtifactError(f"{path} is not a readable CSV: {exc}") from exc

    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ArtifactError(
            f"{path}: missing required column(s) {missing} — ARCHITECTURE "
            f"§13.1 requires {list(REQUIRED_COLUMNS)}; rebuild with "
            f"'python scripts/prepare_data.py'"
        )

    # Stamps: parse strictly; a bad token is corruption, not missingness.
    try:
        index = pd.DatetimeIndex(pd.to_datetime(raw["ts_utc"], utc=True,
                                                errors="raise"))
    except (ValueError, TypeError) as exc:
        raise ArtifactError(
            f"{path}: 'ts_utc' contains values that are not UTC ISO-8601 "
            f"timestamps ({exc}); first offending value near row "
            f"{_first_bad_row(raw['ts_utc'])}"
        ) from exc
    index.name = "ts_utc"

    if index.has_duplicates:
        dup = index[index.duplicated()][0]
        raise ArtifactError(f"{path}: duplicate timestamp {dup} in 'ts_utc'")
    if not index.is_monotonic_increasing:
        violations = index.to_series().diff().dropna() < pd.Timedelta(0)
        first = int(violations.values.argmax()) + 1
        raise ArtifactError(
            f"{path}: timestamps are not monotonic increasing at row {first} "
            f"({index[first - 1]} -> {index[first]})"
        )
    steps = index.to_series().diff().dropna()
    bad_steps = steps != pd.Timedelta(minutes=TICK_MINUTES)
    if bad_steps.any():
        pos = int(bad_steps.values.argmax()) + 1
        raise ArtifactError(
            f"{path}: grid step at row {pos} is {steps.iloc[pos - 1]} "
            f"(between {index[pos - 1]} and {index[pos]}); ARCHITECTURE "
            f"§13.1 requires a regular {TICK_MINUTES}-minute UTC grid"
        )

    frame = pd.DataFrame(index=index)
    for col in REQUIRED_COLUMNS[1:]:
        values = pd.to_numeric(raw[col].str.strip().replace("", None),
                               errors="coerce")
        corrupted = values.isna() & raw[col].str.strip().ne("")
        if corrupted.any():
            pos = int(corrupted.values.argmax())
            raise ArtifactError(
                f"{path}: column '{col}' row {pos} ({index[pos]}) has a "
                f"non-numeric value {raw[col].iloc[pos]!r}; blank = missing "
                f"(ARCHITECTURE §13.1), any other token is corruption"
            )
        # assign as a plain array: `values` carries a RangeIndex while the
        # frame is indexed by ts_utc, and pandas would align them to all-NaN
        frame[col] = values.astype(float).to_numpy()
    return frame


def _first_bad_row(series: pd.Series) -> int:
    for pos, value in enumerate(series):
        try:
            pd.Timestamp(value)
        except (ValueError, TypeError):
            return pos
    return -1


def slice_window(df: pd.DataFrame, start, end,
                 warmup_ticks: int = 0) -> pd.DataFrame:
    """Slice a scenario window with warmup history (TASKS T009).

    Returns rows in [start − warmup_ticks · 15 min, end] inclusive. The
    warmup slice must exist in the dataset; a window that would begin before
    the data does is an ArtifactError, not a silent shorter window."""
    start_ts = _as_utc_timestamp(start, "window start")
    end_ts = _as_utc_timestamp(end, "window end")
    warm_start = start_ts - pd.Timedelta(minutes=TICK_MINUTES * warmup_ticks)
    if len(df) == 0:
        raise ArtifactError("dataset is empty; cannot slice a window")
    lo, hi = df.index[0], df.index[-1]
    if warm_start < lo:
        raise ArtifactError(
            f"window start {start_ts} with warmup_ticks={warmup_ticks} begins "
            f"at {warm_start}, before the dataset starts ({lo}); move the "
            f"window or add data coverage"
        )
    if end_ts > hi:
        raise ArtifactError(
            f"window end {end_ts} is after the dataset ends ({hi})"
        )
    out = df.loc[warm_start:end_ts]
    if out.empty:
        raise ArtifactError(
            f"window [{warm_start} .. {end_ts}] selects no rows from the dataset"
        )
    return out


# ---------------------------------------------------------------------------
# model artifact
# ---------------------------------------------------------------------------

def load_model_artifact(path: str | Path) -> dict:
    """Load and validate `artifacts/model.json` (ARCHITECTURE §6.8, no
    pickle). Returns the parsed dict; raises ArtifactError on absence, bad
    JSON, or missing required keys."""
    path = Path(path)
    if not path.exists():
        raise ArtifactError(
            f"model artifact not found: {path} — run "
            f"'python scripts/fit_models.py' (TASKS T012–T015) to fit and "
            f"save it"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            artifact = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArtifactError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(artifact, dict):
        raise ArtifactError(f"{path} must contain a JSON object")

    missing = [k for k in MODEL_REQUIRED_KEYS if k not in artifact]
    if missing:
        raise ArtifactError(
            f"{path}: missing required key(s) {missing} — ARCHITECTURE §6.8 "
            f"defines the model.json schema; refit with "
            f"'python scripts/fit_models.py'"
        )
    if not isinstance(artifact["tick_minutes"], int) or artifact["tick_minutes"] <= 0:
        raise ArtifactError(f"{path}: 'tick_minutes' must be a positive integer")
    if not isinstance(artifact["variants"], list) or not artifact["variants"]:
        raise ArtifactError(f"{path}: 'variants' must be a non-empty list")
    for i, variant in enumerate(artifact["variants"]):
        if not isinstance(variant, dict):
            raise ArtifactError(f"{path}: variants[{i}] must be an object")
        vmissing = [k for k in VARIANT_REQUIRED_KEYS if k not in variant]
        if vmissing:
            raise ArtifactError(
                f"{path}: variants[{i}] ('{variant.get('name', '?')}') is "
                f"missing required key(s) {vmissing} — ARCHITECTURE §6.8"
            )
    for section in ("ratings", "station_params"):
        roles = artifact.get(section)
        if not isinstance(roles, dict) or not roles:
            raise ArtifactError(
                f"{path}: '{section}' must be a non-empty mapping keyed by "
                f"station role — ARCHITECTURE §6.8"
            )
        missing_roles = [r for r in ("A", "T", "B", "C") if r not in roles]
        if missing_roles:
            raise ArtifactError(
                f"{path}: '{section}' is missing station role(s) {missing_roles}"
            )
    return artifact
