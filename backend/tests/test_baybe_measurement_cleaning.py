"""P1-4: DOE 前缺失值检测与清洗。

Contract under test (pure unit tests on the cleaning layer — no BayBE run):
1. Rows with all-NaN metrics are dropped (no signal).
2. Rows with partially-NaN metrics are dropped (no imputation of optimizer
   targets — fabricating evidence is worse than dropping the row).
3. A measurement frame missing a searchspace factor column (e.g.
   ``cure_temperature_c``) fails closed with a ValueError naming the factor,
   instead of an obscure BayBE error downstream.
4. A clean frame passes through unchanged; empty input is returned as-is.
"""
from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from app.services.engines.baybe_engine import (
    _clean_measurement_dataframe,
    _prepare_measurement_dataframe,
)


def _df():
    return pd.DataFrame(
        [
            {"epoxy": 50.0, "cure_temperature_c": 120.0, "salt_spray_h": 480.0},
            {"epoxy": 55.0, "cure_temperature_c": 130.0, "salt_spray_h": float("nan")},
            {"epoxy": 60.0, "cure_temperature_c": 140.0, "salt_spray_h": float("nan")},
        ]
    )


def test_partial_nan_rows_dropped_not_imputed():
    df = pd.DataFrame(
        [
            {"epoxy": 50.0, "salt_spray_h": 480.0, "adhesion_mpa": 5.0},
            {"epoxy": 55.0, "salt_spray_h": float("nan"), "adhesion_mpa": 4.5},  # partial
            {"epoxy": 60.0, "salt_spray_h": 510.0, "adhesion_mpa": float("nan")},  # partial
        ]
    )
    cleaned, report = _clean_measurement_dataframe(df, ["salt_spray_h", "adhesion_mpa"])
    assert report == {"dropped_all_nan": 0, "dropped_partial_nan": 2, "kept": 1}
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["epoxy"] == 50.0
    # No NaN survives into the frame BayBE will see.
    assert not cleaned[["salt_spray_h", "adhesion_mpa"]].isna().any().any()


def test_all_nan_rows_dropped():
    df = pd.DataFrame(
        [
            {"epoxy": 50.0, "salt_spray_h": 480.0},
            {"epoxy": 55.0, "salt_spray_h": float("nan")},
        ]
    )
    # Second metric column entirely NaN → that row is "all NaN".
    df["adhesion_mpa"] = [5.0, float("nan")]
    df.loc[1, "salt_spray_h"] = float("nan")
    cleaned, report = _clean_measurement_dataframe(df, ["salt_spray_h", "adhesion_mpa"])
    assert report["dropped_all_nan"] == 1
    assert report["kept"] == 1


def test_missing_factor_column_fails_closed():
    df = pd.DataFrame([{"epoxy": 50.0, "salt_spray_h": 480.0}])
    with pytest.raises(ValueError, match="cure_temperature_c"):
        _clean_measurement_dataframe(
            df, ["salt_spray_h"], expected_params=["epoxy", "cure_temperature_c"]
        )


def test_missing_factor_check_skipped_when_no_expected_params():
    df = pd.DataFrame([{"epoxy": 50.0, "salt_spray_h": 480.0}])
    cleaned, report = _clean_measurement_dataframe(df, ["salt_spray_h"])
    assert report["kept"] == 1 and len(cleaned) == 1


def test_clean_frame_passes_through():
    df = pd.DataFrame(
        [
            {"epoxy": 50.0, "cure_temperature_c": 120.0, "salt_spray_h": 480.0},
            {"epoxy": 55.0, "cure_temperature_c": 130.0, "salt_spray_h": 510.0},
        ]
    )
    cleaned, report = _clean_measurement_dataframe(
        df, ["salt_spray_h"], expected_params=["epoxy", "cure_temperature_c"]
    )
    assert report == {"dropped_all_nan": 0, "dropped_partial_nan": 0, "kept": 2}
    assert cleaned.equals(df.reset_index(drop=True))


def test_empty_frame_returned_as_is():
    empty = pd.DataFrame()
    cleaned, report = _clean_measurement_dataframe(empty, ["salt_spray_h"])
    assert cleaned is empty
    assert report["kept"] == 0


def test_prepare_wires_cleaning_and_keeps_contract():
    df = _df()
    out = _prepare_measurement_dataframe(
        df, ["salt_spray_h"], expected_params=["epoxy", "cure_temperature_c"]
    )
    # 1 kept (full row), 2 dropped (partial NaN) — and the metric column
    # contract still holds for what remains.
    assert len(out) == 1
    assert not out["salt_spray_h"].isna().any()


def test_prepare_raises_on_missing_factor():
    df = pd.DataFrame([{"epoxy": 50.0, "salt_spray_h": 480.0}])
    with pytest.raises(ValueError, match="cure_temperature_c"):
        _prepare_measurement_dataframe(
            df, ["salt_spray_h"], expected_params=["epoxy", "cure_temperature_c"]
        )
