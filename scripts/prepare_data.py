"""Prepare the reach dataset: download, cache, align (TASKS T007 + T008).

The ONLY network-using script in the repository (NFR-07): after it completes,
everything else runs offline. Downloads 15-minute stage (00065) and discharge
(00060) for stations A/T/B/C plus hourly basin precipitation at the configured
rain points, then writes data/reach.csv (ARCHITECTURE §13.1) and
data/data_report.json.

USGS access path (T007): the maintained dataretrieval `waterdata.get_continuous`
client is tried first; on any failure the legacy `nwis.get_iv` module is used.
Responses are cached in data/raw/ and never re-downloaded unless --force.

Usage:
    python scripts/prepare_data.py [--force] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from stageproof.settings import ConfigError, load_settings  # noqa: E402

PARAM_STAGE = "00065"
PARAM_DISCHARGE = "00060"
KIND_PCAMES = (("stage", PARAM_STAGE), ("q", PARAM_DISCHARGE))
ROLES = ("A", "T", "B", "C")
GRID_MINUTES = 15
MAX_INTERP_TICKS = 4  # tiny-gap interpolation limit: 4 x 15 min = 1 hour (T008)
WARMUP_DAYS = 30
RETRY_TRIES = 3
RETRY_BACKOFF_S = (5.0, 15.0)
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
RAW_DIR = REPO_ROOT / "data" / "raw"
REACH_CSV = REPO_ROOT / "data" / "reach.csv"
REPORT_JSON = REPO_ROOT / "data" / "data_report.json"


class FetchError(RuntimeError):
    """A download failed after retries (T007)."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _retry(label: str, fn):
    last = "unknown error"
    for attempt in range(RETRY_TRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retry any transport failure
            last = repr(exc)[:200]
            if attempt < RETRY_TRIES - 1:
                wait = RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)]
                print(f"  retry {attempt + 1}/{RETRY_TRIES - 1} for {label} "
                      f"after {wait:.0f}s ({last})")
                time.sleep(wait)
    raise FetchError(f"{label} failed after {RETRY_TRIES} tries: {last}")


def _chunks(start: date, end: date, years: int):
    cur = start
    while cur <= end:
        nxt = min(date(cur.year + years, cur.month, cur.day) - timedelta(days=1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def _to_utc_index(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    return idx


# ---------------------------------------------------------------------------
# USGS stage / discharge (T007)
# ---------------------------------------------------------------------------

def _normalize_nwis(df: pd.DataFrame, pcode: str) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        cand = [c for c in df.columns
                if str(c[0]) == pcode and str(c[1]) == pcode]
        if not cand:
            cand = [c for c in df.columns
                    if str(c[0]) == pcode and str(c[1]).startswith(pcode + "_00003")]
        if not cand:
            cand = [c for c in df.columns if str(c[0]) == pcode]
    else:
        cand = ([c for c in df.columns if str(c) == pcode]
                or [c for c in df.columns if str(c).startswith(pcode + "_00003")]
                or [c for c in df.columns if pcode in str(c)])
    if not cand:
        raise RuntimeError(f"no value column for parameter {pcode}")
    values = pd.to_numeric(df[cand[0]], errors="coerce")
    # Honor the tz_cd column when the service returned site-local naive times.
    index = pd.DatetimeIndex(df.index)
    if index.tz is None and "tz_cd" in df.columns:
        offsets = df["tz_cd"].astype(str).map({"EST": -5, "EDT": -4,
                                               "CST": -6, "CDT": -5,
                                               "UTC": 0}).fillna(0)
        index = index - pd.to_timedelta(offsets.to_numpy(), unit="h")
    out = pd.DataFrame({"value": values.to_numpy()}, index=_to_utc_index(index))
    return out.dropna(subset=["value"]).sort_index()


def _normalize_waterdata(df: pd.DataFrame, pcode: str) -> pd.DataFrame:
    cols = {str(c).strip().lower(): c for c in df.columns}
    if "parameter_code" in cols:
        df = df[df[cols["parameter_code"]].astype(str) == pcode]
        if df.empty:
            raise RuntimeError(f"no rows for parameter {pcode}")
    tcol = next((cols[k] for k in ("time", "datetime", "date_time", "timestamp")
                 if k in cols), None)
    vcol = next((cols[k] for k in ("value", "result") if k in cols), None)
    if tcol is None or vcol is None:
        raise RuntimeError(f"unexpected waterdata columns: {list(df.columns)[:8]}")
    out = pd.DataFrame({
        "value": pd.to_numeric(df[vcol], errors="coerce").to_numpy(),
    }, index=pd.DatetimeIndex(pd.to_datetime(df[tcol], utc=True)))
    return out.dropna(subset=["value"]).sort_index()


def _fetch_usgs_maintained(site: str, pcode: str, start: date, end: date) -> pd.DataFrame:
    from dataretrieval import waterdata
    frames = []
    for s, e in _chunks(start, end, years=3):
        # Overlap chunk ends by 1 day: the maintained endpoint's time-filter
        # boundary semantics are looser than the legacy service; duplicates are
        # deduped in get_series, and overlap makes seam gaps impossible.
        df, _meta = waterdata.get_continuous(
            monitoring_location_id=f"USGS-{site}", parameter_code=pcode,
            time=[s.isoformat(), min(e + timedelta(days=1), end + timedelta(days=1)).isoformat()])
        if df is not None and not df.empty:
            frames.append(_normalize_waterdata(df, pcode))
    if not frames:
        raise RuntimeError("waterdata.get_continuous returned no data")
    return pd.concat(frames).sort_index()


def _fetch_usgs_legacy(site: str, pcode: str, start: date, end: date) -> pd.DataFrame:
    from dataretrieval import nwis
    frames = []
    for s, e in _chunks(start, end, years=1):
        df, _meta = nwis.get_iv(sites=[site], start=s.isoformat(),
                                end=e.isoformat(), parameterCd=pcode)
        if df is not None and not df.empty:
            frames.append(_normalize_nwis(df, pcode))
    if not frames:
        raise RuntimeError("nwis.get_iv returned no data")
    return pd.concat(frames).sort_index()


def get_series(role: str, site: str, pcode: str, start: date, end: date,
               force: bool) -> tuple[pd.DataFrame, str, bool]:
    """Return (series, source, fetched_now) for one station-parameter pair."""
    path = RAW_DIR / f"{role}_{site}_{pcode}_iv.csv"
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    start_iso, end_iso = start.isoformat(), end.isoformat()
    if not force and path.exists():
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("start", "") <= start_iso and meta.get("end", "") >= end_iso:
            cached = pd.read_csv(path, index_col="ts_utc", parse_dates=True)
            cached.index = _to_utc_index(cached.index)
            return cached.loc[(pd.Timestamp(start_iso, tz="UTC")):
                              (pd.Timestamp(end_iso, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(minutes=GRID_MINUTES))], meta.get("source", "cache"), False
    try:
        df = _retry(f"{site}/{pcode} maintained", lambda: _fetch_usgs_maintained(site, pcode, start, end))
        source = "waterdata.get_continuous"
    except (FetchError, Exception) as exc:  # noqa: BLE001 - maintained client is early-release; fall back
        print(f"  maintained client unavailable for {site}/{pcode}; "
              f"falling back to legacy nwis ({repr(exc)[:120]})")
        df = _retry(f"{site}/{pcode} legacy", lambda: _fetch_usgs_legacy(site, pcode, start, end))
        source = "nwis.get_iv"
    df = df[~df.index.duplicated(keep="first")].sort_index()
    out = df.copy()
    out.index.name = "ts_utc"
    out.to_csv(path)
    meta_path.write_text(json.dumps({
        "source": source, "start": start_iso, "end": end_iso,
        "rows": int(len(out)),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    return df, source, True


# ---------------------------------------------------------------------------
# Rain (T008): IEM ASOS hourly p01i
# ---------------------------------------------------------------------------

def _fetch_rain_station(stn: str, start: date, end: date) -> pd.Series:
    frames = []
    for s, e in _chunks(start, end, years=1):
        params = {
            "station": stn, "data": "p01i",
            "year1": s.year, "month1": s.month, "day1": s.day,
            "year2": e.year, "month2": e.month, "day2": e.day,
            "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
            "elev": "no", "missing": "M", "trace": "T", "report_type": "3",
        }
        text = _retry(f"IEM {stn} {s.year}", lambda p=params: requests.get(
            IEM_URL, params=p, timeout=90).text)

        def parse(t=text, s=s, e=e):
            df = pd.read_csv(io.StringIO(t))
            if "valid" not in df.columns or "p01i" not in df.columns:
                raise RuntimeError(f"unexpected IEM columns: {list(df.columns)[:6]}")
            raw = df["p01i"].astype(str).str.strip()
            vals = pd.to_numeric(raw.where(~raw.isin(["M", ""])), errors="coerce")
            vals = vals.mask(raw == "T", 0.0)  # trace -> 0.0 mm
            idx = pd.DatetimeIndex(pd.to_datetime(df["valid"], utc=True))
            return pd.Series(vals.to_numpy() * 25.4, index=idx, name=stn)

        frames.append(parse())
        time.sleep(1.0)  # be polite to the IEM server
    if not frames:
        raise FetchError(f"IEM returned no data for {stn}")
    ser = pd.concat(frames).sort_index()
    ser = ser[~ser.index.duplicated(keep="first")].dropna()
    return ser


def get_rain(stn: str, start: date, end: date, force: bool) -> tuple[pd.Series, bool]:
    path = RAW_DIR / f"rain_{stn}.csv"
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    start_iso, end_iso = start.isoformat(), end.isoformat()
    if not force and path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        if meta.get("start", "") <= start_iso and meta.get("end", "") >= end_iso:
            cached = pd.read_csv(path, index_col="ts_utc", parse_dates=True)
            cached.index = _to_utc_index(cached.index)
            return cached.iloc[:, 0].dropna(), False
    ser = _fetch_rain_station(stn, start, end)
    out = ser.rename("rain_mm").to_frame()
    out.index.name = "ts_utc"
    out.to_csv(path)
    meta_path.write_text(json.dumps({
        "source": "IEM ASOS p01i", "start": start_iso, "end": end_iso,
        "rows": int(len(out)),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    return ser, True


def _weighted_hourly_rain(series_by_stn: dict, weights: dict) -> pd.Series:
    vals = pd.concat(series_by_stn, axis=1)           # columns = station ids
    w = pd.Series({stn: weights[stn] for stn in vals.columns})
    mask = vals.notna()
    wsum = mask.mul(w, axis=1).sum(axis=1)
    mean = vals.mul(w, axis=1).sum(axis=1).div(wsum.where(wsum > 0))
    # METAR observations arrive at e.g. :53; bucket each obs to the END of its
    # hour (ceil) so the value is never visible before its window completes
    # (ARCHITECTURE §13.1: the value stamped H is available from H).
    mean.index = mean.index.ceil("1h")
    mean = mean.groupby(level=0).mean().sort_index()
    return mean.clip(lower=0.0)


# ---------------------------------------------------------------------------
# Alignment and output (T008)
# ---------------------------------------------------------------------------

def _interp_tiny(series: pd.Series, max_ticks: int) -> tuple[pd.Series, int]:
    """Linear-interpolate interior gaps of at most max_ticks consecutive points."""
    filled = series.copy()
    isnan = series.isna()
    n_interp = 0
    groups = (isnan != isnan.shift()).cumsum()
    for _, grp in series[isnan].groupby(groups[isnan]):
        lo, hi = grp.index.min(), grp.index.max()
        if lo == series.index.min() or hi == series.index.max():
            continue  # never invent leading/trailing values
        if len(grp) <= max_ticks:
            filled.loc[lo:hi] = series.interpolate(method="time").loc[lo:hi]
            n_interp += len(grp)
    return filled, n_interp


def _gap_stats(values: pd.Series, step_minutes: int) -> dict:
    n_missing = int(values.isna().sum())
    isnan = values.isna()
    longest = 0
    gaps_gt_1h = 0
    if n_missing:
        groups = (isnan != isnan.shift()).cumsum()
        runs = [len(g) for _, g in values[isnan].groupby(groups[isnan])]
        longest = max(runs)
        gaps_gt_1h = sum(1 for r in runs if r * step_minutes > 60)
    total = len(values)
    return {
        "missing": n_missing,
        "missing_pct": round(100.0 * n_missing / total, 3) if total else 0.0,
        "longest_gap_ticks": longest,
        "gaps_gt_1h": gaps_gt_1h,
    }


def _timing_check(grid: pd.DatetimeIndex, rain_grid: pd.Series,
                  hourly: pd.Series) -> dict:
    """Verify rain at row ts equals the hourly value stamped floor(ts, 1h)."""
    samples = []
    ok = True
    candidates = [t for t in (grid[0] + pd.Timedelta(hours=6),
                              grid[len(grid) // 2],
                              grid[len(grid) // 2] + pd.Timedelta(minutes=47))
                  if t in grid]
    for t in candidates:
        h = t.floor("1h")
        got = rain_grid.loc[t] if t in rain_grid.index else float("nan")
        expected = hourly.get(h, float("nan"))
        row_ok = (pd.isna(got) and pd.isna(expected)) or (got == expected)
        ok = ok and row_ok
        samples.append({"ts_utc": t.isoformat(), "hour_stamped": h.isoformat(),
                        "value": None if pd.isna(got) else round(float(got), 6),
                        "expected": None if pd.isna(expected) else round(float(expected), 6),
                        "ok": bool(row_ok)})
    return {"rule": "row ts gets hourly value stamped floor(ts, 1h); "
                    "METAR obs at :53 bucketed to the ceil hour (no future leak)",
            "pass": bool(ok), "samples": samples}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="re-download even when cached files cover the range")
    parser.add_argument("--start", help="override range start (YYYY-MM-DD, UTC)")
    parser.add_argument("--end", help="override range end (YYYY-MM-DD, UTC)")
    args = parser.parse_args(argv)

    settings = load_settings(REPO_ROOT / "config", REPO_ROOT / ".env")
    reach = settings.reach
    splits = reach.splits
    for name in ("train", "calibration", "test"):
        window = splits.get(name) or {}
        if not window.get("start") or not window.get("end"):
            raise ConfigError(
                f"config/reach.yaml: splits.{name}.start/end must be filled "
                f"(complete T006 before running prepare_data)")
    split_starts = [date.fromisoformat(splits[n]["start"])
                    for n in ("train", "calibration", "test")]
    split_ends = [date.fromisoformat(splits[n]["end"])
                  for n in ("train", "calibration", "test")]

    start = date.fromisoformat(args.start) if args.start \
        else min(split_starts) - timedelta(days=WARMUP_DAYS)
    end = date.fromisoformat(args.end) if args.end else date.today()

    sites = {}
    for role in ROLES:
        entry = reach.station(role)
        if not entry.get("usgs_site"):
            raise ConfigError(
                f"config/reach.yaml: stations.{role}.usgs_site is empty — "
                f"complete T006 (reach selection) before running prepare_data")
        sites[role] = str(entry["usgs_site"])

    rain_points = reach.rain.get("points") or []
    if not rain_points:
        raise ConfigError(
            "config/reach.yaml: rain.points is empty — complete T006 first")
    for i, p in enumerate(rain_points):
        if not p.get("station") or p.get("weight") is None:
            raise ConfigError(
                f"config/reach.yaml: rain.points[{i}] needs 'station' (IEM ASOS id), "
                f"'lat', 'lon' and 'weight'")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "grid": {"step_minutes": GRID_MINUTES, "start": start.isoformat(),
                 "end": end.isoformat()},
        "usgs": {}, "rain": {"source": reach.rain.get("source"), "stations": {}},
        "columns": {}, "timing_check": {}, "assumptions": {
            "max_interp_minutes": MAX_INTERP_TICKS * GRID_MINUTES,
            "warmup_days": WARMUP_DAYS,
            "rain_trace": "T treated as 0.0 mm",
            "rain_bucket": "hour ending at bucket start (ceil of METAR obs time) "
                           "so no future leak is possible",
        },
    }

    # --- download stage + discharge ----------------------------------------
    series: dict = {}
    for role in ROLES:
        for kind, pcode in KIND_PCAMES:
            label = f"{role}_{kind}"
            print(f"fetching {label} ({sites[role]}/{pcode}) {start}..{end} ...")
            df, source, fetched = get_series(role, sites[role], pcode, start, end,
                                             args.force)
            series[(role, pcode)] = df
            report["usgs"][label] = {
                "site": sites[role], "parameter": pcode, "source": source,
                "fetched": fetched, "rows": int(len(df)),
                "start_utc": df.index.min().isoformat() if len(df) else None,
                "end_utc": df.index.max().isoformat() if len(df) else None,
            }
            print(f"  {label}: {len(df)} rows via {source} "
                  f"({'fetched' if fetched else 'cache'})")

    # --- download rain -------------------------------------------------------
    series_by_stn: dict = {}
    weights: dict = {}
    for p in rain_points:
        stn = str(p["station"])
        print(f"fetching rain {stn} {start}..{end} ...")
        ser, fetched = get_rain(stn, start, end, args.force)
        series_by_stn[stn] = ser
        weights[stn] = float(p["weight"])
        report["rain"]["stations"][stn] = {
            "lat": p.get("lat"), "lon": p.get("lon"), "weight": p["weight"],
            "fetched": fetched, "rows": int(len(ser)),
            "start_utc": ser.index.min().isoformat() if len(ser) else None,
            "end_utc": ser.index.max().isoformat() if len(ser) else None,
        }
        print(f"  rain {stn}: {len(ser)} hourly rows ({'fetched' if fetched else 'cache'})")

    hourly = _weighted_hourly_rain(series_by_stn, weights)
    print(f"basin-mean hourly rain: {len(hourly)} rows "
          f"({hourly.notna().sum()} non-missing)")

    # --- align to the 15-minute grid ----------------------------------------
    grid_start = pd.Timestamp(start, tz="UTC")
    grid_end = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(minutes=GRID_MINUTES)
    grid = pd.date_range(grid_start, grid_end, freq=f"{GRID_MINUTES}min", tz="UTC")
    data: dict = {"ts_utc": grid}

    interp_counts: dict = {}
    for role in ROLES:
        for kind, pcode in KIND_PCAMES:
            col = f"{role}_{kind}"
            v = series[(role, pcode)]["value"].reindex(grid)
            v, n_interp = _interp_tiny(v, MAX_INTERP_TICKS)
            data[col] = v.to_numpy()
            interp_counts[col] = n_interp

    floor_hours = grid.floor("1h")
    data["rain_mm_prev_hr"] = hourly.reindex(floor_hours).to_numpy()

    frame = pd.DataFrame(data)
    frame.index = grid

    # --- checks --------------------------------------------------------------
    timing = _timing_check(grid, pd.Series(data["rain_mm_prev_hr"], index=grid),
                           hourly)
    report["timing_check"] = timing
    for role in ROLES:
        for kind, _pcode in KIND_PCAMES:
            col = f"{role}_{kind}"
            stats = _gap_stats(frame[col], GRID_MINUTES)
            stats["interpolated_points"] = interp_counts[col]
            report["columns"][col] = stats
    stats = _gap_stats(frame["rain_mm_prev_hr"], GRID_MINUTES)
    report["columns"]["rain_mm_prev_hr"] = stats
    report["grid"]["rows"] = int(len(frame))

    monotic = frame["ts_utc"].is_monotonic_increasing
    step_ok = (frame["ts_utc"].diff().dropna() == pd.Timedelta(minutes=GRID_MINUTES)).all()
    report["grid"]["monotonic"] = bool(monotic)
    report["grid"]["regular_step"] = bool(step_ok)

    # --- write ---------------------------------------------------------------
    out = frame.copy()
    out["ts_utc"] = out["ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(REACH_CSV, index=False, na_rep="")
    report["reach_csv"] = {"path": str(REACH_CSV.relative_to(REPO_ROOT)),
                           "columns": list(out.columns)}
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"\nwrote {REACH_CSV} ({len(frame)} rows) and {REPORT_JSON}")
    print("coverage / gaps:")
    for col, stats in report["columns"].items():
        print(f"  {col:<16} missing {stats['missing_pct']:>6.2f}%  "
              f"longest gap {stats['longest_gap_ticks']} ticks  "
              f"gaps>1h {stats['gaps_gt_1h']}  "
              f"interp {stats.get('interpolated_points', 0)}")
    print(f"rain timing rule: {'PASS' if timing['pass'] else 'FAIL'}")
    if not (monotic and step_ok):
        print("ERROR: grid is not a monotonic regular 15-minute UTC index")
        return 1
    if not timing["pass"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except FetchError as exc:
        print(f"FETCH ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
