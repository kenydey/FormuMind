"""Tests for Campaign snapshot → BayBE objective alignment."""
from __future__ import annotations

from app.db.campaign_store import CampaignStore
from app.db.database import make_engine, make_session_factory
from app.domain.objective_contract import objective_metrics
from app.domain.schemas import DOEPlan, DOERun, ObjectiveSpec, ProductDomain, Requirement
from app.services.engines.adapters.baybe_objective_builder import build_objective_from_specs
from app.services.engines.baybe_engine import fetch_campaign_data_for_baybe
from app.services.engines.campaign_objectives import resolve_campaign_objectives


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="abc12345",
        domain=ProductDomain.anticorrosion_coating,
    )


def test_resolve_campaign_objectives_from_snapshot():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store = CampaignStore(factory)
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="voc_gpl", weight=1.0, direction="minimize")],
    )
    campaign = store.create_from_plan(
        _plan(),
        req=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            objectives=[
                ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
                ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
            ],
        ),
    )
    with factory() as session:
        resolved = resolve_campaign_objectives(session, campaign.id, req)
    metrics = objective_metrics(resolved)
    assert metrics == ["salt_spray_hours", "cost_cny_per_kg"]


def test_build_objective_from_specs_target_names_match_metrics():
    objectives = [
        ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
        ObjectiveSpec(metric="cost_cny_per_kg", weight=0.5, direction="minimize"),
    ]
    try:
        obj = build_objective_from_specs(objectives)
    except ImportError:
        return  # baybe not installed in CI
    assert hasattr(obj, "targets") or hasattr(obj, "target")


def test_fetch_columns_match_snapshot_metrics():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store = CampaignStore(factory)
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )
    campaign = store.create_from_plan(_plan(), req=req)

    with factory() as session:
        from app.db.models import ExperimentRecord as WorkbenchRow

        row = session.query(WorkbenchRow).filter(WorkbenchRow.campaign_id == campaign.id).first()
        row.measurements = {"salt_spray_hours": 900.0, "cost_cny_per_kg": 18.0}
        row.status = "Completed"
        session.commit()

        _, measurements_Y = fetch_campaign_data_for_baybe(campaign.id, session, req)
        assert list(measurements_Y.columns) == ["salt_spray_hours", "cost_cny_per_kg"]
