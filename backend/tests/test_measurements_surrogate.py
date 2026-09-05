"""B15: surrogate measurements must not poison BayBE with 0.0 on failure."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement
from app.services.engines.adapters.measurements_adapter import (
    surrogate_measurements_from_plan,
)


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize"),
        ],
    )


def _plan(*naturals: dict) -> SimpleNamespace:
    runs = [SimpleNamespace(id=f"r{i}", natural=n) for i, n in enumerate(naturals)]
    return SimpleNamespace(runs=runs)


def test_surrogate_skips_row_when_predict_raises(monkeypatch):
    from app.pipeline import reconstruct
    from app.services import predictor

    monkeypatch.setattr(reconstruct, "formulation_from_factors", lambda *a, **k: object())
    monkeypatch.setattr(
        predictor, "predict", lambda form: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    df = surrogate_measurements_from_plan(_plan({"x": 1.0}), _req())
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_surrogate_skips_row_when_metric_missing(monkeypatch):
    from app.pipeline import reconstruct
    from app.services import predictor

    monkeypatch.setattr(reconstruct, "formulation_from_factors", lambda *a, **k: object())
    monkeypatch.setattr(predictor, "predict", lambda form: {"cost": 1.0})  # no salt_spray_hours

    df = surrogate_measurements_from_plan(_plan({"x": 1.0}), _req())
    assert df.empty


def test_surrogate_keeps_complete_rows(monkeypatch):
    from app.pipeline import reconstruct
    from app.services import predictor

    monkeypatch.setattr(reconstruct, "formulation_from_factors", lambda *a, **k: object())
    monkeypatch.setattr(
        predictor, "predict", lambda form: {"salt_spray_hours": 720.0, "cost": 2.0}
    )

    df = surrogate_measurements_from_plan(_plan({"x": 1.0}, {"x": 2.0}), _req())
    assert len(df) == 2
    assert list(df["salt_spray_hours"]) == [720.0, 720.0]
    assert 0.0 not in list(df["salt_spray_hours"])
