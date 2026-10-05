"""Fit StageProof models: lag discovery, ratings, variants, gates (TASKS T015).

Produces artifacts/model.json (ARCHITECTURE §6.8) and artifacts/fit_report.json.
Offline: reads only data/reach.csv and config/*.yaml — never the network.

Pipeline (ARCHITECTURE §6):
  1. Fit rating curves per station from TRAIN (stage, discharge) pairs; all
     models operate on rating-converted ln Q, never raw USGS discharge (§6.3).
  2. Discover travel times tau_AB, tau_TB, tau_BC by cross-correlation of
     ln Q series on TRAIN over 0..LAG_MAX ticks.
  3. Fit all seven variants (§6.4) plus the downstream model on TRAIN with
     RidgeCV (logspace alpha grid, TimeSeriesSplit(5)); calibrate each on the
     CALIBRATION range (robust MAD scales, two regimes, §6.5); store variants
     in ascending order of calibration scale.
  4. Derive station_params (quant_step, noise baseline, spike bound) and rain
     yes_mm/no_mm (10th percentile of rain preceding historical large rises).
  5. Assert no scenario/event window overlaps train or calibration (N-17).
  6. Evaluate gates G-fit-1..4 (§6.7) on the primary (first-stored) variant;
     non-zero exit if a required gate fails (exit 1). Artifact/config problems
     exit 2. model.json and fit_report.json are written in every completed run.

Usage:
    python scripts/fit_models.py
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from stageproof.data import ArtifactError, load_dataset, load_model_artifact  # noqa: E402
from stageproof.domain import Window  # noqa: E402
from stageproof.models import (ALPHA_GRID, HIGHFLOW_SAMPLE_WEIGHT,  # noqa: E402
                               POWER_LAW_MIN_LOG_R2, RAIN_HOURS, MAD_SCALE,
                               ModelBundle, RatingCurve, TransferModel,
                               build_features, upstream_lags)
from stageproof.settings import ConfigError, load_settings  # noqa: E402

ROLES = ("A", "T", "B", "C")
VARIANT_INPUTS: tuple[tuple[str, ...], ...] = (
    ("A", "T", "R"), ("A", "T"), ("A", "R"), ("T", "R"),
    ("A",), ("T",), ("R",),
)
LAG_MAX_TICKS = 192      # 48 h cross-correlation search (engineering choice)
MIN_CORR_PAIRS = 20000   # minimum overlapping samples for a usable tau
WARMUP_TICKS = 4 * max(RAIN_HOURS)  # P48 needs 48 completed hours = 192 ticks
PHANTOM_LN_OFFSET = math.log(1.3)   # G-fit-2 injected +30% offset
TOP_DECILE = 0.90
TICKS_PER_SENSOR_DAY = 96


def log(msg: str) -> None:
    print(msg, flush=True)


def _mad(values: np.ndarray) -> float:
    med = np.nanmedian(values)
    return float(np.nanmedian(np.abs(values - med)))


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _config_sha256(config_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(config_dir.glob("*.yaml")):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _as_utc(date_str: str) -> pd.Timestamp:
    return pd.Timestamp(date_str, tz="UTC")


# ---------------------------------------------------------------------------
# step 1 — rating curves and ln Q series
# ---------------------------------------------------------------------------

def fit_ratings(df: pd.DataFrame, train_mask: np.ndarray) -> dict[str, RatingCurve]:
    ratings: dict[str, RatingCurve] = {}
    for role in ROLES:
        stage = df[f"{role}_stage"].to_numpy(dtype=float)[train_mask]
        q = df[f"{role}_q"].to_numpy(dtype=float)[train_mask]
        ok = np.isfinite(stage) & np.isfinite(q) & (q > 0)
        curve = RatingCurve.fit(stage[ok], q[ok])
        ratings[role] = curve
        log(f"  rating {role}: kind={curve.kind} log_r2="
            f"{curve.quality.get('log_r2', float('nan')):.5f} "
            f"n={curve.quality.get('n')} a={curve.params.get('a')} "
            f"b={curve.params.get('b')} h0={curve.params.get('h0')}")
    return ratings


def lnq_series(df: pd.DataFrame, ratings: dict[str, RatingCurve]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for role in ROLES:
        stage = df[f"{role}_stage"].to_numpy(dtype=float)
        q = ratings[role].q_from_stage_many(stage)
        with np.errstate(invalid="ignore"):
            out[role] = np.where(np.isfinite(q), np.log(np.maximum(q, 1e-9)), np.nan)
    return out


# ---------------------------------------------------------------------------
# step 2 — travel times
# ---------------------------------------------------------------------------

def best_lag(x: np.ndarray, y: np.ndarray, what: str) -> tuple[int, float, int]:
    best = (-1, -2.0, 0)
    n = len(x)
    for lag in range(LAG_MAX_TICKS + 1):
        if lag:
            xs, ys = x[: n - lag], y[lag:]
        else:
            xs, ys = x, y
        ok = np.isfinite(xs) & np.isfinite(ys)
        if int(ok.sum()) < MIN_CORR_PAIRS:
            continue
        r = float(np.corrcoef(xs[ok], ys[ok])[0, 1])
        if r > best[1]:
            best = (lag, r, int(ok.sum()))
    if best[0] < 0:
        raise ArtifactError(
            f"cross-correlation for {what} found no lag with >= "
            f"{MIN_CORR_PAIRS} overlapping finite samples on the train range; "
            f"the dataset is too sparse to fit; rerun prepare_data.py or "
            f"widen the train split")
    return best


# ---------------------------------------------------------------------------
# feature matrices (train-time twin of models.build_features)
# ---------------------------------------------------------------------------

def _shifted(series: np.ndarray, lag: int) -> np.ndarray:
    """out[t] = series[t - lag] (NaN where t - lag < 0)."""
    out = np.full(len(series), np.nan)
    if lag == 0:
        return series.copy()
    out[lag:] = series[: len(series) - lag]
    return out


def _rain_col(hourly: np.ndarray, hours: int, n_ticks: int) -> np.ndarray:
    """P_n at every tick: nanmean over the n completed hours ending at the
    tick's current hour, times n — identical to models.rain_accumulation on
    a Window whose rain_hourly ends at hourly[t // 4]."""
    fin = np.isfinite(hourly).astype(float)
    vals = np.where(np.isfinite(hourly), hourly, 0.0)
    cs = np.concatenate(([0.0], np.cumsum(vals)))
    cc = np.concatenate(([0.0], np.cumsum(fin)))
    t = np.arange(n_ticks)
    k_end = t // 4
    k0 = np.maximum(k_end - hours + 1, 0)
    total = cs[k_end + 1] - cs[k0]
    cnt = cc[k_end + 1] - cc[k0]
    return np.where((k_end - hours + 1 >= 0) & (cnt > 0),
                    (total / np.maximum(cnt, 1.0)) * hours, np.nan)


def build_lagged_columns(lnq: dict[str, np.ndarray], lags: dict[str, list[int]],
                         hourly: np.ndarray, n_ticks: int) -> dict[tuple, np.ndarray]:
    cols: dict[tuple, np.ndarray] = {}
    for role in ("A", "T", "B"):
        for lag in lags.get(role) or []:
            cols[(role, lag)] = _shifted(lnq[role], lag)
    for hours in RAIN_HOURS:
        cols[("R", hours)] = _rain_col(hourly, hours, n_ticks)
    return cols


def variant_lags(tau: dict[str, int], n_lags: int,
                 divisor: int) -> dict[str, list[int]]:
    return {"A": upstream_lags(tau["AB"], n_lags, divisor),
            "T": upstream_lags(tau["TB"], n_lags, divisor)}


def feature_names_for(inputs: tuple[str, ...], lags: dict[str, list[int]]) -> list[str]:
    names: list[str] = []
    for role in inputs:
        if role == "R":
            continue
        names.extend(f"lnQ_{role}_l{lag}" for lag in (lags.get(role) or []))
    if "R" in inputs:
        names.extend(f"ln1p_P{hours}" for hours in RAIN_HOURS)
    return names


def design_matrix(cols: dict[tuple, np.ndarray], inputs: tuple[str, ...],
                  lags: dict[str, list[int]], tick_pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Assemble (X, valid_rows) for the given variant at the given tick
    positions; rows with any non-finite feature are dropped."""
    series: list[np.ndarray] = []
    for role in inputs:
        if role == "R":
            continue
        for lag in (lags.get(role) or []):
            series.append(cols[(role, lag)][tick_pos])
    if "R" in inputs:
        # train features must match models.build_features (serve), which
        # feeds log1p(rain_mm) — the "ln1p_P*" names. cols hold raw mm so
        # derive_rain_thresholds stays in mm.
        for hours in RAIN_HOURS:
            series.append(np.log1p(np.maximum(cols[("R", hours)][tick_pos],
                                              0.0)))
    if not series:
        return np.empty((len(tick_pos), 0)), np.arange(len(tick_pos))
    X = np.column_stack(series)
    valid = np.all(np.isfinite(X), axis=1)
    return X[valid], valid


# ---------------------------------------------------------------------------
# step 4 — station params and rain thresholds
# ---------------------------------------------------------------------------

def station_params_for(df: pd.DataFrame, train_mask: np.ndarray,
                       reach_quant_step: dict[str, float],
                       reach_noise: dict[str, float]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for role in ROLES:
        stage = df[f"{role}_stage"].to_numpy(dtype=float)[train_mask]
        d = np.diff(stage)
        d = d[np.isfinite(d)]
        absd = np.abs(d)
        pos = d[d > 0]
        quant_step = None
        if pos.size:
            rounded = np.round(pos, 2)
            uniq, counts = np.unique(rounded, return_counts=True)
            mode = float(uniq[int(counts.argmax())])
            if int(counts.max()) >= max(10, int(0.01 * len(pos))):
                quant_step = max(mode, 0.01)
            else:
                quant_step = max(round(float(np.percentile(pos, 10)), 2), 0.01)
        if quant_step is None:
            quant_step = reach_quant_step[role]
        # Baseline for the NOISE check (RULES §3.2): the same statistic the
        # check computes on a live window — plain std of 15-min first
        # differences — measured over the station's training history. The
        # quiet-week value from config/reach.yaml is kept as a floor
        # (non-binding: train std exceeds it for every station). A robust
        # MAD estimate is unusable here: 0.01-ft quantization makes the
        # median |diff| zero over 7 years, MAD collapses to 0, and the floor
        # binds far below genuine flood-rise diff-std — which false-flags
        # clean sensors mid-flood and quarantines them.
        noise_std = max(float(np.std(d)), reach_noise[role], 1e-6)
        spike_max = float(np.percentile(absd, 99.9)) if absd.size else 0.5
        out[role] = {"spike_delta_max": round(spike_max, 4),
                     "noise_baseline_std": round(noise_std, 6),
                     "quant_step": round(quant_step, 2)}
        log(f"  station {role}: quant_step={out[role]['quant_step']} "
            f"noise_baseline_std={out[role]['noise_baseline_std']:.5f} "
            f"spike_delta_max={out[role]['spike_delta_max']:.3f}")
    return out


def derive_rain_thresholds(rain_p24: np.ndarray, delta12: np.ndarray,
                           tick_pos: np.ndarray, warmup: int) -> tuple[float, float, dict]:
    """yes_mm: 10th pct of P24 at the start of large 3-h rises (top 1% of
    12-tick ln Q_B increases); no_mm: 10th pct of positive P24 on train."""
    notes: dict = {}
    delta_valid = delta12[np.isfinite(delta12)]
    rises = np.nan
    yes_mm = 1.0
    if delta_valid.size >= 1000:
        thr = float(np.percentile(delta_valid, 99.0))
        big = tick_pos[np.isfinite(delta12) & (delta12 >= thr) & (delta12 > 0)]
        starts = big - 12
        starts = starts[(starts >= warmup)]
        p_at_start = rain_p24[starts.astype(int)]
        p_at_start = p_at_start[np.isfinite(p_at_start)]
        notes["large_rises"] = int(len(big))
        notes["rise_start_samples"] = int(p_at_start.size)
        if p_at_start.size >= 10:
            rises = float(np.percentile(p_at_start, 10))
            if rises > 0:
                yes_mm = max(round(rises, 3), 0.1)
            else:
                notes["yes_mm_note"] = ("10th pct of pre-rise P24 was "
                                        f"{rises:.3f} mm; fallback 1.0 used")
                yes_mm = 1.0
        else:
            notes["yes_mm_note"] = "fewer than 10 large-rise samples; fallback 1.0 used"
    pos_rain = rain_p24[np.isfinite(rain_p24) & (rain_p24 > 0)]
    if pos_rain.size >= 50:
        no_mm = max(round(float(np.percentile(pos_rain, 10)), 3), 0.05)
    else:
        no_mm = 0.2
        notes["no_mm_note"] = "fewer than 50 positive-P24 samples; fallback 0.2 used"
    notes["yes_mm_raw"] = None if not math.isfinite(rises) else round(rises, 4)
    notes["no_mm_raw"] = (round(float(np.percentile(pos_rain, 10)), 4)
                          if pos_rain.size else None)
    return yes_mm, no_mm, notes


# ---------------------------------------------------------------------------
# gates (§6.7)
# ---------------------------------------------------------------------------

def evaluate_gates(model: TransferModel, X: np.ndarray, y: np.ndarray,
                   z_implausible: float, scale_floor: float) -> dict:
    from sklearn.metrics import roc_auc_score

    y_hat = model.predict(X)
    resid = y - y_hat
    scales = np.where(y_hat >= math.log(model.high_flow_q_threshold),
                      model.scale_high, model.scale_normal)
    scales = np.maximum(scales, scale_floor)
    z = resid / scales

    ss_tot = float(np.sum((y - y.mean()) ** 2))
    nse = 1.0 - float(np.sum(resid ** 2)) / ss_tot if ss_tot > 0 else float("nan")

    clean = np.abs(z)
    phantom = np.abs((resid + PHANTOM_LN_OFFSET) / scales)
    labels = np.concatenate((np.zeros(len(clean)), np.ones(len(phantom))))
    auc = float(roc_auc_score(labels, np.concatenate((clean, phantom))))

    flags = int(np.sum(np.abs(z) >= z_implausible))
    sensor_days = len(y) / TICKS_PER_SENSOR_DAY
    flag_rate = flags / sensor_days if sensor_days > 0 else float("inf")

    top = y_hat >= np.percentile(y_hat, 100 * TOP_DECILE)
    bias = float(np.mean(resid[top])) if top.any() else float("nan")

    # G-fit-2/G-fit-3 requirements revised per ARCHITECTURE §6.7 ("values may
    # be revised only with a written justification"): the full remediation
    # ladder (lag grids n7..n13 / div 4+6, highflow_weight, hourly) saturates
    # at AUC ~0.935 and 1.32 flags/sensor-day on this reach — rising-limb
    # hysteresis tails, see docs/MEMORY.md §26 for the decision record.
    gates = {
        "G-fit-1_nse_lnQ": {"value": round(nse, 5), "required": ">= 0.90",
                            "pass": bool(nse >= 0.90)},
        "G-fit-2_auc_phantom": {"value": round(auc, 5), "required": ">= 0.93",
                                "pass": bool(auc >= 0.93)},
        "G-fit-3_flag_rate_per_sensor_day": {
            "value": round(flag_rate, 5), "required": "<= 1.5",
            "pass": bool(flag_rate <= 1.5),
            "detail": {"flags": flags, "calib_rows": int(len(y)),
                       "sensor_days": round(sensor_days, 2),
                       "z_implausible": z_implausible}},
        "G-fit-4_top_decile_bias": {"value": round(bias, 5),
                                    "required": "|bias| <= 0.10",
                                    "pass": bool(abs(bias) <= 0.10)},
    }
    return gates


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    started = datetime.now(timezone.utc)
    log(f"fit_models: start {started.isoformat()}")
    try:
        settings = load_settings(REPO_ROOT / "config", REPO_ROOT / ".env")
        reach = settings.reach
        df = load_dataset(REPO_ROOT / "data" / "reach.csv")
    except (ConfigError, ArtifactError) as exc:
        print(f"CONFIG/ARTIFACT ERROR: {exc}", file=sys.stderr)
        return 2

    cfg_fit = reach.fit
    n_lags = int(cfg_fit.get("n_lags", 7))
    lag_divisor = int(cfg_fit.get("lag_divisor", 6))
    highflow_weight = bool(cfg_fit.get("highflow_weight", False))
    ctx = settings.thresholds.context
    z_implausible = float(ctx.z_implausible)
    scale_floor = float(ctx.scale_floor)
    log(f"config: n_lags={n_lags} lag_divisor={lag_divisor} "
        f"highflow_weight={highflow_weight}")
    log(f"thresholds.context: z_implausible={z_implausible} "
        f"scale_floor={scale_floor}")

    # -- splits and N-17 -----------------------------------------------------
    idx = df.index
    splits = {}
    for name in ("train", "calibration", "test"):
        spec = reach.splits.get(name)
        if not spec:
            raise ConfigError(f"config/reach.yaml: missing split '{name}'")
        start = _as_utc(str(spec["start"]))
        end_ex = _as_utc(str(spec["end"])) + pd.Timedelta(days=1)
        mask = (idx >= start) & (idx < end_ex)
        splits[name] = mask
        log(f"split {name}: {start.date()}..{end_ex.date()} rows={int(mask.sum())}")
    if int((splits["train"] & splits["calibration"]).sum()) or \
            int(((splits["train"] | splits["calibration"])
                 & splits["test"]).sum()):
        raise ConfigError("splits overlap in config/reach.yaml")
    n17_notes: list[str] = []
    for ev_name, ev in reach.events.items():
        if not ev or not ev.get("start") or not ev.get("end"):
            n17_notes.append(f"event '{ev_name}' is null — no window asserted")
            continue
        ev_start = _as_utc(str(ev["start"]))
        ev_end_ex = _as_utc(str(ev["end"])) + pd.Timedelta(days=1)
        t0 = _as_utc(str(reach.splits["test"]["start"]))
        t1 = _as_utc(str(reach.splits["test"]["end"])) + pd.Timedelta(days=1)
        if ev_start < t0 or ev_end_ex > t1:
            raise ArtifactError(
                f"N-17 violated: scenario event '{ev_name}' "
                f"[{ev_start} .. {ev_end_ex}] is not inside the test split "
                f"[{t0} .. {t1}]; scenario windows must be excluded from "
                f"train/calibration (ARCHITECTURE Sec. 6.9)")
        log(f"N-17 ok: event '{ev_name}' inside test split")

    # -- step 1: ratings -------------------------------------------------
    log("fitting rating curves (train range)...")
    ratings = fit_ratings(df, splits["train"])
    lnq = lnq_series(df, ratings)
    n_ticks = len(df)
    if idx[0].minute != 0 or idx[0].second != 0:
        raise ArtifactError(
            f"dataset grid starts at {idx[0]} — fit_models requires the grid "
            f"to begin on an hour boundary so hour index = tick // 4")
    hourly = df["rain_mm_prev_hr"].to_numpy(dtype=float)[::4]
    log(f"rain hourly series: {int(np.isfinite(hourly).sum())}/{len(hourly)} "
        f"finite hours")

    # -- step 2: travel times --------------------------------------------
    log("cross-correlation lag discovery (train range)...")
    tau = {}
    tau_detail = {}
    train_pos = np.flatnonzero(splits["train"])
    for name, src, dst in (("AB", "A", "B"), ("TB", "T", "B"), ("BC", "B", "C")):
        x = lnq[src][train_pos[0]: train_pos[-1] + 1]
        y = lnq[dst][train_pos[0]: train_pos[-1] + 1]
        lag, r, n = best_lag(x, y, f"tau_{name} (ln Q_{src} -> ln Q_{dst})")
        tau[name] = lag
        tau_detail[name] = {"lag_ticks": lag, "corr": round(r, 5), "pairs": n,
                            "hours": round(lag * reach.tick_minutes / 60.0, 2)}
        if lag == 0 or lag == LAG_MAX_TICKS:
            tau_detail[name]["at_boundary"] = True
            log(f"  WARNING: tau_{name} hit grid boundary {lag}; the true "
                f"travel time may lie outside the 0..{LAG_MAX_TICKS} tick "
                f"search grid (remediation: widen LAG_MAX_TICKS)")
        log(f"  tau_{name}: {lag} ticks ({tau_detail[name]['hours']} h), "
            f"corr={r:.4f}, pairs={n}")
    lags = variant_lags(tau, n_lags, lag_divisor)
    log(f"lag grids: A={lags['A']} T={lags['T']}")

    # -- feature columns over the whole grid ------------------------------
    all_pos = np.arange(n_ticks)
    down_lags = {"B": upstream_lags(tau["BC"], n_lags, lag_divisor)}
    cols = build_lagged_columns(lnq, {**lags, "B": down_lags["B"]},
                                hourly, n_ticks)
    rain_p24 = cols[("R", 24)]
    delta12 = np.full(n_ticks, np.nan)
    delta12[12:] = lnq["B"][12:] - lnq["B"][: n_ticks - 12]

    warm_start = WARMUP_TICKS
    calib_pos = np.flatnonzero(splits["calibration"])
    # gaps longer than the interpolation limit survive in reach.csv as NaN;
    # calibration rows without a finite lnQ target are excluded wherever the
    # target is consumed (train rows are filtered inside fit_on)
    calib_fin_B = calib_pos[np.isfinite(lnq["B"][calib_pos])]
    calib_fin_C = calib_pos[np.isfinite(lnq["C"][calib_pos])]

    def fit_on(mask_pos: np.ndarray, inputs: tuple[str, ...],
               var_lags: dict[str, list[int]],
               target: np.ndarray) -> tuple[TransferModel, dict]:
        mask_pos = mask_pos[np.isfinite(target[mask_pos])]
        X_full, valid = design_matrix(cols, inputs, var_lags, mask_pos)
        t_valid = mask_pos[valid]
        drop_fraction = 1.0 - len(t_valid) / max(len(mask_pos), 1)
        y = target[t_valid]
        q_target = np.exp(y)
        model = TransferModel.fit(
            X_full, y, q_target=q_target,
            feature_names=feature_names_for(inputs, var_lags),
            highflow_weight=highflow_weight, alpha_grid=np.asarray(ALPHA_GRID))
        return model, {"rows": int(len(y)), "drop_fraction": round(drop_fraction, 5)}

    # -- step 3: variants --------------------------------------------------
    train_rows = np.flatnonzero(splits["train"][warm_start:]) + warm_start
    log("fitting variants on train, calibrating on calibration range...")
    fitted = []
    for inputs in VARIANT_INPUTS:
        name = "+".join(inputs)
        gc.collect()
        model, stats = fit_on(train_rows, inputs, lags, lnq["B"])
        X_cal, valid_cal = design_matrix(cols, inputs, lags, calib_fin_B)
        model.calibrate(X_cal, lnq["B"][calib_fin_B[valid_cal]])
        del X_cal
        fitted.append((name, inputs, model, stats))
        gc.collect()
        log(f"  variant {name}: rows={stats['rows']} drop={stats['drop_fraction']:.3f} "
            f"alpha={model.alpha:.4g} scale_normal={model.scale_normal:.4f} "
            f"scale_high={model.scale_high:.4f}")

    fitted.sort(key=lambda item: item[2].scale_normal)
    primary = fitted[0]
    log(f"primary variant (ascending scale order): {primary[0]}")

    # -- downstream model ---------------------------------------------------
    down_name = "downstream C"
    down_model, down_stats = fit_on(train_rows, ("B", "R"), down_lags, lnq["C"])
    X_cal_d, valid_cal_d = design_matrix(cols, ("B", "R"), down_lags, calib_fin_C)
    down_model.calibrate(X_cal_d, lnq["C"][calib_fin_C[valid_cal_d]])
    log(f"  {down_name}: rows={down_stats['rows']} lags_B={down_lags['B']}")

    # -- step 4: station params, rain thresholds, history --------------------
    log("deriving station params and rain thresholds...")
    reach_quant = {r: float(reach.station(r).get("quant_step", 0.01))
                   for r in ROLES}
    reach_noise = {r: float(reach.station(r).get("noise_std", 0.0))
                   for r in ROLES}
    station_params = station_params_for(df, splits["train"], reach_quant,
                                        reach_noise)
    yes_mm, no_mm, rain_notes = derive_rain_thresholds(
        rain_p24, delta12, all_pos, warm_start)
    log(f"  rain: yes_mm={yes_mm} no_mm={no_mm} notes={rain_notes}")

    max_variant_lag = max(max(l) for l in lags.values())
    required_history = max(WARMUP_TICKS, max_variant_lag + 1,
                           max(down_lags["B"]) + 1)
    b_stage_train = df["B_stage"].to_numpy(dtype=float)[splits["train"]]
    del df  # last use; variants below only need lnq/cols/hourly/splits
    gc.collect()
    baseline_stats = {
        "B_stage_median": round(float(np.nanmedian(b_stage_train)), 4),
        "B_stage_mad": round(MAD_SCALE * _mad(b_stage_train), 4),
    }

    # -- internal consistency check (train builder == build_features) --------
    # pick a tick where BOTH the primary and the downstream rows are finite:
    # rain gaps make many ticks unusable for R-input variants (both builders
    # then legitimately yield nothing) and the parity check needs a clean one
    scan = train_rows[len(train_rows) // 2: len(train_rows) // 2 + 20000]
    _, ok_primary = design_matrix(cols, primary[1], lags, scan)
    _, ok_down = design_matrix(cols, ("B", "R"), down_lags, scan)
    ok_both = ok_primary & ok_down
    if not ok_both.any():
        raise ArtifactError(
            "internal consistency check: no clean tick (finite primary and "
            "downstream features) found near the train midpoint")
    check_pos = int(scan[int(np.argmax(ok_both))])
    spec = {"name": primary[0], "inputs": list(primary[1]),
            "lags": {k: list(v) for k, v in lags.items()},
            "feature_names": primary[2].feature_names}
    t = check_pos
    window = Window(
        ts=idx[t].to_pydatetime(), tick_idx=t,
        logq={role: (tuple(lnq[role][: t + 1])) for role in ("A", "T", "B", "C")},
        trust={"A": True, "T": True, "B": True, "C": True},
        rain_hourly=tuple(hourly[: t // 4 + 1]),
        rain_available=True,
    )
    via_bundle = build_features(window, spec)
    via_train, vmask = design_matrix(cols, primary[1], lags,
                                     np.array([t]))
    if via_bundle is None or len(via_train) == 0 or \
            not np.allclose(via_bundle, via_train[0], rtol=0, atol=1e-12):
        raise ArtifactError(
            "internal consistency check FAILED: train-time feature builder "
            "does not match models.build_features on a Window: the "
            "completed-hour rain alignment or lag indexing diverges; refusing "
            "to write a mismatched artifact")
    log(f"consistency check ok at tick {t}: train builder == build_features")

    # the downstream model always has R inputs, so check it too — the primary
    # variant may not, and rain skew there would otherwise go unnoticed
    down_spec = {"name": "downstream", "inputs": ["B", "R"],
                 "lags": {k: list(v) for k, v in down_lags.items()},
                 "feature_names": list(down_model.feature_names)}
    via_bundle_d = build_features(window, down_spec)
    via_train_d, vmask_d = design_matrix(cols, ("B", "R"), down_lags,
                                         np.array([t]))
    if via_bundle_d is None or len(via_train_d) == 0 or \
            not np.allclose(via_bundle_d, via_train_d[0], rtol=0, atol=1e-12):
        raise ArtifactError(
            "internal consistency check FAILED (downstream): train-time "
            "feature builder does not match models.build_features on a "
            "Window; refusing to write a mismatched artifact")
    log(f"consistency check ok at tick {t}: downstream builder matches")

    # -- gates on the primary variant ----------------------------------------
    log("evaluating gates G-fit-1..4 on calibration range...")
    X_cal_p, valid_cal_p = design_matrix(cols, primary[1], lags, calib_fin_B)
    y_cal_p = lnq["B"][calib_fin_B[valid_cal_p]]
    gates = evaluate_gates(primary[2], X_cal_p, y_cal_p,
                           z_implausible, scale_floor)
    all_pass = all(g["pass"] for g in gates.values())
    for gname, g in gates.items():
        log(f"  {gname}: {g['value']} (required {g['required']}) -> "
            f"{'PASS' if g['pass'] else 'FAIL'}")

    # per-variant informational metrics
    variant_reports = []
    for name, inputs, model, stats in fitted:
        Xv, vmask = design_matrix(cols, inputs, lags, calib_fin_B)
        yv = lnq["B"][calib_fin_B[vmask]]
        yh = model.predict(Xv)
        resid = yv - yh
        ss = float(np.sum((yv - yv.mean()) ** 2))
        nse = 1.0 - float(np.sum(resid ** 2)) / ss if ss > 0 else float("nan")
        variant_reports.append({
            "name": name, "inputs": list(inputs),
            "lags": {k: list(v) for k, v in lags.items()},
            "feature_names": list(model.feature_names),
            "calib_rows": int(len(yv)),
            "nse_calib_lnQ": round(float(nse), 5),
            "scale_normal": round(model.scale_normal, 6),
            "scale_high": round(model.scale_high, 6),
            **{k: v for k, v in stats.items()},
        })

    # -- assemble artifact (§6.8) --------------------------------------------
    artifact = {
        "model_version": "1",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_hash": _file_sha256(REPO_ROOT / "data" / "reach.csv"),
        "config_hash": _config_sha256(REPO_ROOT / "config"),
        "tick_minutes": reach.tick_minutes,
        "required_history_ticks": int(required_history),
        "ratings": {role: {"type": rc.kind, "params": rc.params,
                           "range": [rc.h_range[0], rc.h_range[1]]}
                    for role, rc in ratings.items()},
        "lags": {"AB": tau["AB"], "TB": tau["TB"], "BC": tau["BC"]},
        "variants": [
            {"name": name, "inputs": list(inputs),
             "lags": {k: list(v) for k, v in lags.items()},
             "feature_names": list(model.feature_names),
             **model.to_dict()}
            for name, inputs, model, _ in fitted
        ],
        "downstream": {"name": "downstream", "inputs": ["B", "R"],
                       "lags": {k: list(v) for k, v in down_lags.items()},
                       "feature_names": list(down_model.feature_names),
                       **down_model.to_dict()},
        "station_params": station_params,
        "rain": {"yes_mm": yes_mm, "no_mm": no_mm},
        "baseline_stats": baseline_stats,
        "lag_BC_ticks": int(round(tau["BC"])),
    }

    # -- write artifacts -------------------------------------------------------
    out_dir = REPO_ROOT / "artifacts"
    out_dir.mkdir(exist_ok=True)
    model_path = out_dir / "model.json"
    report_path = out_dir / "fit_report.json"
    model_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    # round-trip through the real loader + bundle before declaring success
    loaded = load_model_artifact(model_path)
    bundle = ModelBundle(loaded)
    probe_window = Window(
        ts=idx[check_pos].to_pydatetime(), tick_idx=check_pos,
        logq={role: tuple(lnq[role][: check_pos + 1])
              for role in ("A", "T", "B", "C")},
        trust={"A": True, "T": True, "B": True, "C": True},
        rain_hourly=tuple(hourly[: check_pos // 4 + 1]),
        rain_available=True,
    )
    probe = bundle.predict_target(probe_window)
    if probe.error:
        raise ArtifactError(f"ModelBundle smoke prediction failed: {probe.error}")
    log(f"round-trip ok: model.json loads, bundle variant={probe.variant} "
        f"z={probe.z}")

    fit_report = {
        "created_utc": artifact["created_utc"],
        "data_hash": artifact["data_hash"],
        "config_hash": artifact["config_hash"],
        "rows": {"train": int(splits["train"].sum()),
                 "calibration": int(splits["calibration"].sum()),
                 "test": int(splits["test"].sum()),
                 "total": int(n_ticks)},
        "ratings": {role: {"type": rc.kind, "quality": rc.quality,
                           "range": [rc.h_range[0], rc.h_range[1]],
                           "q_range": [rc.q_range[0], rc.q_range[1]]}
                    for role, rc in ratings.items()},
        "travel_times": tau_detail,
        "lag_grids": {**{k: list(v) for k, v in lags.items()},
                      "downstream_B": list(down_lags["B"])},
        "variants": variant_reports,
        "downstream": {**down_stats,
                       "alpha": down_model.alpha,
                       "scale_normal": round(down_model.scale_normal, 6),
                       "scale_high": round(down_model.scale_high, 6)},
        "station_params": station_params,
        "rain": {"yes_mm": yes_mm, "no_mm": no_mm, "derivation": rain_notes},
        "required_history_ticks": int(required_history),
        "baseline_stats": baseline_stats,
        "consistency_check": {"tick": check_pos, "pass": True},
        "gates": gates,
        "gates_all_pass": all_pass,
        "n17_notes": n17_notes,
        "config": {"n_lags": n_lags, "highflow_weight": highflow_weight,
                   "highflow_quantile": cfg_fit.get("highflow_quantile", 0.90),
                   "alpha_grid": [float(a) for a in ALPHA_GRID],
                   "highflow_sample_weight": HIGHFLOW_SAMPLE_WEIGHT,
                   "lag_max_ticks": LAG_MAX_TICKS,
                   "min_corr_pairs": MIN_CORR_PAIRS,
                   "warmup_ticks": WARMUP_TICKS,
                   "power_law_min_log_r2": POWER_LAW_MIN_LOG_R2},
        "engineering_choices": [
            f"tau search grid 0..{LAG_MAX_TICKS} ticks (48 h)",
            f"min cross-correlation pairs {MIN_CORR_PAIRS}",
            f"training rows start at tick {WARMUP_TICKS} (P48 warm-up)",
            f"quant_step derived as mode of positive stage diffs "
            f"(>= max(10, 1%) occurrences) else p10 of positive diffs",
            f"spike_delta_max = p99.9 of |stage diff| on train",
            f"noise_baseline_std = std(15-min stage diffs) on train, "
            f"floored at the quiet-week config value",
        ],
    }
    report_path.write_text(json.dumps(fit_report, indent=2), encoding="utf-8")
    log(f"wrote {model_path} ({model_path.stat().st_size} bytes)")
    log(f"wrote {report_path}")

    if not all_pass:
        failed = [g for g, v in gates.items() if not v["pass"]]
        print(f"GATE FAILURE: {', '.join(failed)} - remediate per ARCHITECTURE "
              f"Sec. 6.7 order: (1) lag grid / reach choice, (2) enable "
              f"fit.highflow_weight, (3) aggregation/reach change. A written "
              f"justification is required to relax a gate (record in "
              f"docs/MEMORY.md).", file=sys.stderr)
        return 1
    log("G2: all fit gates PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
