"""Tests for stageproof/data.py (TASKS T009). Synthetic fixtures only —
these do not touch data/reach.csv or the network."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from stageproof.data import (ArtifactError, REQUIRED_COLUMNS, load_dataset,
                             load_model_artifact, slice_window)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def make_dataset_csv(path, n=20, start="2020-11-10T05:00:00Z", **overrides):
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    frame = pd.DataFrame(1.5, index=idx, columns=list(REQUIRED_COLUMNS[1:]))
    frame["rain_mm_prev_hr"] = 0.0
    frame.index.name = "ts_utc"
    for col, value in overrides.items():
        frame[col] = value
    frame.reset_index().to_csv(path, index=False, na_rep="")
    return path


@pytest.fixture
def dataset_csv(tmp_path):
    return make_dataset_csv(tmp_path / "reach.csv")


def valid_artifact():
    def variant(name, inputs):
        return {
            "name": name, "inputs": inputs, "lags": {"AB": 12, "TB": 4},
            "feature_names": ["lnQ_A_l0"], "mean": [0.0], "std": [1.0],
            "coef": [1.0], "intercept": 0.0, "alpha": 1.0,
            "scale_normal": 0.1, "scale_high": 0.2,
            "high_flow_q_threshold": 10000.0,
        }

    return {
        "model_version": "1", "created_utc": "2026-10-04T00:00:00Z",
        "data_hash": "ab" * 32, "config_hash": "cd" * 32,
        "tick_minutes": 15, "required_history_ticks": 48,
        "ratings": {r: {"type": "power_law", "params": {"a": 1.0, "b": 1.5, "h0": 0.0},
                        "range": [1.0, 20.0]} for r in "ATBC"},
        "lags": {"AB": 12, "TB": 4, "BC": 8},
        "variants": [variant("A+T+R", ["A", "T", "R"]), variant("A", ["A"])],
        "downstream": {"coef": [1.0], "intercept": 0.0},
        "station_params": {r: {"spike_delta_max": 2.0, "noise_baseline_std": 0.01,
                               "quant_step": 0.01} for r in "ATBC"},
        "rain": {"yes_mm": 2.0, "no_mm": 0.2},
        "baseline_stats": {},
        "lag_BC_ticks": 8,
    }


# ---------------------------------------------------------------------------
# load_dataset
# ---------------------------------------------------------------------------

class TestLoadDataset:
    def test_valid_dataset_loads(self, dataset_csv):
        df = load_dataset(dataset_csv)
        assert list(df.columns) == list(REQUIRED_COLUMNS[1:])
        assert df.index.name == "ts_utc"
        assert df.index.tz is not None
        assert (df.index.to_series().diff().dropna()
                == pd.Timedelta(minutes=15)).all()
        assert df["B_stage"].dtype == float
        # value-level guard: make_dataset_csv fills 1.5 everywhere but rain
        assert (df["B_stage"] == 1.5).all()
        assert (df["rain_mm_prev_hr"] == 0.0).all()

    def test_blank_is_missing_not_corruption(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv", n=10)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df.loc[3, "rain_mm_prev_hr"] = ""
        df.to_csv(path, index=False)
        loaded = load_dataset(path)
        assert np.isnan(loaded["rain_mm_prev_hr"].iloc[3])

    def test_missing_file(self, tmp_path):
        with pytest.raises(ArtifactError, match="prepare_data"):
            load_dataset(tmp_path / "nope.csv")

    def test_missing_column(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv")
        df = pd.read_csv(path)
        df.drop(columns=["T_q"]).to_csv(path, index=False)
        with pytest.raises(ArtifactError, match="T_q"):
            load_dataset(path)

    def test_non_numeric_token(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv", n=10)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df.loc[4, "B_stage"] = "garbage"
        df.to_csv(path, index=False)
        with pytest.raises(ArtifactError, match="B_stage.*row 4"):
            load_dataset(path)

    def test_bad_timestamp(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv", n=5)
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        df.loc[2, "ts_utc"] = "not-a-time"
        df.to_csv(path, index=False)
        with pytest.raises(ArtifactError, match="ts_utc"):
            load_dataset(path)

    def test_non_monotonic(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv", n=10)
        df = pd.read_csv(path)
        pd.concat([df.iloc[[1, 0]], df.iloc[2:]]).to_csv(path, index=False)
        with pytest.raises(ArtifactError, match="monotonic"):
            load_dataset(path)

    def test_irregular_step(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv", n=10)
        df = pd.read_csv(path)
        df.loc[5, "ts_utc"] = str(pd.Timestamp(df.loc[5, "ts_utc"]) + pd.Timedelta(minutes=5))
        df.to_csv(path, index=False)
        with pytest.raises(ArtifactError, match="grid step"):
            load_dataset(path)

    def test_duplicate_stamp(self, tmp_path):
        path = make_dataset_csv(tmp_path / "reach.csv", n=10)
        df = pd.read_csv(path)
        df.loc[5, "ts_utc"] = df.loc[4, "ts_utc"]
        df.to_csv(path, index=False)
        with pytest.raises(ArtifactError, match="duplicate timestamp"):
            load_dataset(path)


# ---------------------------------------------------------------------------
# slice_window
# ---------------------------------------------------------------------------

class TestSliceWindow:
    def test_slice_with_warmup(self, dataset_csv):
        df = load_dataset(dataset_csv)
        start, end = df.index[10], df.index[14]
        out = slice_window(df, start, end, warmup_ticks=4)
        assert out.index[0] == df.index[6]
        assert out.index[-1] == df.index[14]
        assert len(out) == 9

    def test_warmup_before_data_raises(self, dataset_csv):
        df = load_dataset(dataset_csv)
        with pytest.raises(ArtifactError, match="warmup_ticks=10"):
            slice_window(df, df.index[5], df.index[9], warmup_ticks=10)

    def test_end_after_data_raises(self, dataset_csv):
        df = load_dataset(dataset_csv)
        with pytest.raises(ArtifactError, match="after the dataset ends"):
            slice_window(df, df.index[0], df.index[-1] + pd.Timedelta(minutes=15))

    def test_naive_timestamps_localized_utc(self, dataset_csv):
        df = load_dataset(dataset_csv)
        start, end = df.index[10], df.index[14]
        out = slice_window(df, "2020-11-10T07:30:00", "2020-11-10T08:30:00",
                           warmup_ticks=0)
        assert out.index[0] == start and out.index[-1] == end


# ---------------------------------------------------------------------------
# load_model_artifact
# ---------------------------------------------------------------------------

class TestLoadModelArtifact:
    def test_valid_artifact_loads(self, tmp_path):
        path = tmp_path / "model.json"
        path.write_text(json.dumps(valid_artifact()), encoding="utf-8")
        artifact = load_model_artifact(path)
        assert artifact["tick_minutes"] == 15
        assert len(artifact["variants"]) == 2

    def test_missing_file(self, tmp_path):
        with pytest.raises(ArtifactError, match="fit_models"):
            load_model_artifact(tmp_path / "model.json")

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "model.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ArtifactError, match="not valid JSON"):
            load_model_artifact(path)

    def test_missing_top_level_key(self, tmp_path):
        artifact = valid_artifact()
        del artifact["lag_BC_ticks"]
        path = tmp_path / "model.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        with pytest.raises(ArtifactError, match="lag_BC_ticks"):
            load_model_artifact(path)

    def test_missing_variant_key(self, tmp_path):
        artifact = valid_artifact()
        del artifact["variants"][0]["scale_high"]
        path = tmp_path / "model.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        with pytest.raises(ArtifactError, match="scale_high"):
            load_model_artifact(path)

    def test_ratings_missing_role(self, tmp_path):
        artifact = valid_artifact()
        del artifact["ratings"]["C"]
        path = tmp_path / "model.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        with pytest.raises(ArtifactError, match=r"ratings.*'C'"):
            load_model_artifact(path)

    def test_empty_variants(self, tmp_path):
        artifact = valid_artifact()
        artifact["variants"] = []
        path = tmp_path / "model.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        with pytest.raises(ArtifactError, match="non-empty list"):
            load_model_artifact(path)
