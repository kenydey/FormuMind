"""Tests for adaptive DOE explanations, anomalies, and API enrichment (P1)."""
from __future__ import annotations

import pytest

from app.domain.schemas import (
    DOEFactor,
    DOEPlan,
    DOERun,
    ExperimentRecord,
    ProductDomain,
    Requirement,
)
from app.services.doe_adaptive import build_adaptive_metadata, enrich_active_doe_result
from app.services.doe_anomaly import detect_anomalies
from app.services.doe_explain import (
    build_run_explanations,
    infer_strategy,
    k_nearest_experiments,
    recommend_next_action,
)


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        film_weight_gsm=70,
        cure_temperature_c=80,
        voc_limit_gpl=420,
    )


def _plan(*, n_runs: int = 3) -> DOEPlan:
    factors = [
        DOEFactor(name="Zinc phosphate", low=2.0, high=14.0, unit="wt%"),
        DOEFactor(name="Bisphenol-A epoxy (DGEBA)", low=28.0, high=48.0, unit="wt%"),
    ]
    runs = []
    for i in range(1, n_runs + 1):
        natural = {
            "Zinc phosphate": 4.0 + i,
            "Bisphenol-A epoxy (DGEBA)": 30.0 + i * 2,
        }
        runs.append(
            DOERun(
                run_id=i,
                coded={"Zinc phosphate": -0.5, "Bisphenol-A epoxy (DGEBA)": 0.1},
                natural=natural,
                ai_suggested=True,
            )
        )
    return DOEPlan(design="lhs", factors=factors, runs=runs, domain=ProductDomain.anticorrosion_coating)


def _record(
    factors: dict[str, float],
    measured: float,
    *,
    label: str = "",
) -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors=factors,
        measured={"salt_spray_hours": measured},
        label=label,
    )


class TestInferStrategy:
    def test_exploration_when_few_records(self):
        label, rationale = infer_strategy(3)
        assert label == "exploration"
        assert "3" in rationale

    def test_balanced_mid_range(self):
        label, _ = infer_strategy(12)
        assert label == "balanced"

    def test_exploitation_when_many_records(self):
        label, _ = infer_strategy(25)
        assert label == "exploitation"

    def test_low_budget_shifts_rationale(self):
        label, rationale = infer_strategy(3, budget_remaining=3)
        assert "剩余预算" in rationale
        assert label == "balanced"


class TestRunExplanations:
    def test_explanations_match_suggested_run_count(self):
        req = _req()
        plan = _plan(n_runs=4)
        existing = [
            _record({"Zinc phosphate": 5.0, "Bisphenol-A epoxy (DGEBA)": 32.0}, 520.0, label="exp-a"),
        ]
        expl = build_run_explanations(req, plan, existing, strategy_label="exploration")
        assert len(expl) == 4
        assert all(e.summary for e in expl)

    def test_nearest_experiment_ids_populated(self):
        req = _req()
        plan = _plan(n_runs=2)
        existing = [
            _record({"Zinc phosphate": 5.0, "Bisphenol-A epoxy (DGEBA)": 32.0}, 520.0, label="nearby"),
        ]
        nearest = k_nearest_experiments(plan.runs[0].natural, existing, k=1)
        assert nearest[0][1] == "nearby"

    def test_exploitation_includes_delta_when_data_rich(self):
        req = _req()
        plan = _plan(n_runs=2)
        existing = [
            _record({"Zinc phosphate": 5.0, "Bisphenol-A epoxy (DGEBA)": 35.0}, 600.0),
            _record({"Zinc phosphate": 6.0, "Bisphenol-A epoxy (DGEBA)": 36.0}, 610.0),
            _record({"Zinc phosphate": 7.0, "Bisphenol-A epoxy (DGEBA)": 37.0}, 620.0),
            _record({"Zinc phosphate": 8.0, "Bisphenol-A epoxy (DGEBA)": 38.0}, 630.0),
            _record({"Zinc phosphate": 9.0, "Bisphenol-A epoxy (DGEBA)": 39.0}, 640.0),
            _record({"Zinc phosphate": 10.0, "Bisphenol-A epoxy (DGEBA)": 40.0}, 650.0),
            _record({"Zinc phosphate": 11.0, "Bisphenol-A epoxy (DGEBA)": 41.0}, 660.0),
            _record({"Zinc phosphate": 12.0, "Bisphenol-A epoxy (DGEBA)": 42.0}, 670.0),
        ]
        expl = build_run_explanations(req, plan, existing, strategy_label="exploitation")
        assert any("提升" in e.summary for e in expl)


class TestAnomalyDetection:
    def test_physical_limit_salt_spray(self):
        req = _req()
        existing = [
            _record({"Zinc phosphate": 5.0, "Bisphenol-A epoxy (DGEBA)": 32.0}, 5000.0, label="high-ss"),
        ]
        flags = detect_anomalies(req, existing)
        types = {f.type for f in flags}
        assert "physical_limit" in types

    def test_empty_records_no_anomalies(self):
        assert detect_anomalies(_req(), []) == []

    def test_recommend_action_when_anomalies(self):
        from app.domain.schemas import AnomalyFlag

        action = recommend_next_action(
            n_completed=5,
            strategy_label="balanced",
            budget_remaining=10,
            anomalies=[
                AnomalyFlag(
                    experiment_id="x",
                    type="high_residual",
                    severity="critical",
                    note="test",
                )
            ],
        )
        assert "复测" in action


class TestAdaptiveMetadata:
    def test_build_metadata_fields(self):
        req = _req()
        plan = _plan()
        meta = build_adaptive_metadata(req, plan, [], budget_remaining=15)
        assert meta["strategy_label"] == "exploration"
        assert len(meta["run_explanations"]) == 3
        assert meta["recommended_next_action"]

    def test_enrich_active_doe_result(self):
        from app.domain.schemas import ActiveDoeResult

        req = _req()
        plan = _plan()
        base = ActiveDoeResult(plan=plan, engine="legacy")
        enriched = enrich_active_doe_result(base, req, [], budget_remaining=8)
        assert enriched.strategy_label == "exploration"
        assert len(enriched.run_explanations) == 3


def test_resample_plan_swaps_constraint_violations():
    from app.domain.schemas import DOEFactor, DOEPlan, DOERun
    from app.services.doe_adaptive import resample_plan_for_constraints

    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    factors = [
        DOEFactor(name="Zinc phosphate", low=2.0, high=14.0, unit="wt%"),
    ]
    plan = DOEPlan(
        design="lhs",
        factors=factors,
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 5.0}, ai_suggested=True),
            DOERun(run_id=2, coded={}, natural={"Zinc phosphate": 8.0}, ai_suggested=False),
        ],
        domain=ProductDomain.anticorrosion_coating,
    )
    resampled = resample_plan_for_constraints(req, plan)
    assert len(resampled.runs) == 2


def test_active_doe_api_returns_adaptive_fields():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    payload = {
        "domain": "anticorrosion_coating",
        "substrate": "carbon_steel",
        "salt_spray_hours": 500,
        "film_weight_gsm": 70,
        "cure_temperature_c": 80,
        "cleaning_efficiency": 0,
        "voc_limit_gpl": 420,
        "ph_target": None,
        "notes": "",
        "objectives": [],
        "existing_records": [],
        "n_suggest": 3,
        "doe_design": "lhs",
        "budget_remaining": 12,
    }
    resp = client.post("/api/doe/active", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy_label"] in ("exploration", "balanced", "exploitation")
    assert len(body["run_explanations"]) >= 1
    assert body["recommended_next_action"]
    assert body["budget_remaining"] == 12
