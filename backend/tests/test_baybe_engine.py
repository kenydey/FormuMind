"""Tests for optional baybe Campaign engine."""
from __future__ import annotations

import asyncio

import pytest

from app.db.campaign_store import SqliteCampaignStore
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import (
    DOEPlan,
    DOERun,
    ExperimentRecord,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.services.engines.baybe_engine import BaybeCampaignEngine, fetch_campaign_data_for_baybe
from app.services.engines.doe_registry import baybe_available


REQ = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)


def _workbench_plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="abc12345",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.mark.skipif(not baybe_available(), reason="baybe not installed")
def test_baybe_recommend_roundtrip():
    engine = BaybeCampaignEngine()
    r1 = engine.recommend(REQ, batch_size=2)
    assert r1.engine == "baybe"
    assert len(r1.plan.runs) == 2
    assert r1.campaign_state

    records = [
        ExperimentRecord(
            domain=REQ.domain,
            factors=r1.plan.runs[0].natural,
            measured={"salt_spray_hours": 520.0, "cost_cny_per_kg": 18.5, "sustainability_idx": 0.72},
            source="test",
        )
    ]
    r2 = engine.recommend(
        REQ,
        campaign_state=r1.campaign_state,
        measurements=records,
        batch_size=2,
    )
    assert len(r2.plan.runs) == 2
    assert r2.campaign_state != r1.campaign_state


@pytest.mark.skipif(not baybe_available(), reason="baybe not installed")
def test_baybe_recommend_with_workbench_multi_metric():
    baybe = BaybeCampaignEngine()
    db_engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(db_engine)
    store = SqliteCampaignStore(factory)
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )
    campaign = asyncio.run(store.create_from_plan(_workbench_plan(), req=req))
    rows = asyncio.run(store.list_rows(campaign.id))
    asyncio.run(
        store.batch_sync(
            campaign.id,
            [
                {
                    "id": rows[0].id,
                    "actual_params": {"Zinc phosphate": 9.0},
                    "measurements": {"salt_spray_hours": 900.0, "cost_cny_per_kg": 18.0},
                }
            ],
        )
    )

    actual_X, measurements_Y = fetch_campaign_data_for_baybe(campaign.id, req, store=store)
    assert list(measurements_Y.columns) == ["salt_spray_hours", "cost_cny_per_kg"]

    result = baybe.recommend(
        req,
        batch_size=2,
        workbench_campaign_id=campaign.id,
        store=store,
    )
    assert len(result.plan.runs) == 2
    assert result.campaign_state


def test_baybe_unavailable_raises():
    engine = BaybeCampaignEngine()
    if baybe_available():
        pytest.skip("baybe is installed")
    with pytest.raises(RuntimeError, match="not installed"):
        engine.recommend(REQ, batch_size=1)


def test_pareto_ranking_puts_non_dominated_candidates_first():
    """BayBE optimises a real ParetoObjective and the result was being sorted
    purely by weighted sum, so a candidate nothing dominates could rank below
    one that a third candidate beats outright."""
    from app.domain.schemas import Formulation, Ingredient, ObjectiveSpec, ProductDomain
    from app.services.engines.baybe_engine import _rank_by_pareto_then_score

    def _form(name, salt, cost):
        return Formulation(
            name=name, domain=ProductDomain.anticorrosion_coating,
            ingredients=[Ingredient(name="Epoxy", role="resin", weight_pct=100.0)],
            predicted={"salt_spray_hours": salt, "cost_cny_per_kg": cost},
        )

    objectives = [
        ObjectiveSpec(metric="salt_spray_hours", direction="maximize", weight=0.9),
        ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize", weight=0.1),
    ]
    # "cheap" is on the frontier (nothing dominates it) but the lopsided
    # weights score it last. "dominated" is beaten by "strong" on both axes.
    ranked = [
        (0.90, _form("strong", 1200, 30)),
        (0.70, _form("dominated", 900, 35)),
        (0.40, _form("cheap", 700, 8)),
    ]
    names = [f.name for _, f in _rank_by_pareto_then_score(ranked, objectives, 3)]
    assert names.index("cheap") < names.index("dominated")


def test_pareto_ranking_falls_back_to_scalar_for_one_objective():
    from app.domain.schemas import Formulation, Ingredient, ObjectiveSpec, ProductDomain
    from app.services.engines.baybe_engine import _rank_by_pareto_then_score

    def _form(name, salt):
        return Formulation(
            name=name, domain=ProductDomain.anticorrosion_coating,
            ingredients=[Ingredient(name="Epoxy", role="resin", weight_pct=100.0)],
            predicted={"salt_spray_hours": salt},
        )

    objectives = [ObjectiveSpec(metric="salt_spray_hours", direction="maximize")]
    ranked = [(0.4, _form("low", 700)), (0.9, _form("high", 1200))]
    assert [f.name for _, f in _rank_by_pareto_then_score(ranked, objectives, 2)] == ["high", "low"]
