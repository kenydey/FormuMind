"""P2 预测偏差校准: bias 在 retrain 前计算, 无模型时不阻断, 且写入 loop_history."""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.services import training as training_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    get_settings.cache_clear()
    from app.db.campaign_store import reset_campaign_store
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_bias_empty_when_no_model(monkeypatch, tmp_path):
    from app.db.store import SqlExperimentStore
    from app.services.training import ModelRegistry

    db_path = tmp_path / "bias-empty.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    store = SqlExperimentStore(factory)
    reg = ModelRegistry(store=store)
    monkeypatch.setattr(training_mod, "registry", reg)

    from app.services.workbench_training import _compute_prediction_bias

    rec = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id="",
        factors={"Zinc phosphate": 9.0},
        cure_temperature_c=82.0,
        measured={"salt_spray_hours": 820.0},
        source="workbench",
        label="wb:1:x",
    )
    bias = _compute_prediction_bias([rec], ProductDomain.anticorrosion_coating, "")
    assert bias == {}


def test_bias_computed_before_retrain(monkeypatch, tmp_path):
    from app.db.store import SqlExperimentStore
    from app.services.training import ModelRegistry

    db_path = tmp_path / "bias-before.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    store = SqlExperimentStore(factory)
    reg = ModelRegistry(store=store)
    monkeypatch.setattr(training_mod, "registry", reg)

    settings = get_settings()
    n = settings.min_train_samples
    seeds = [
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            project_id="",
            factors={"Zinc phosphate": 8.0 + i},
            cure_temperature_c=80.0,
            measured={"salt_spray_hours": 700.0 + i * 5},
            source="test",
            label=f"seed-{i}",
        )
        for i in range(n)
    ]
    reg.add(seeds, retrain=True)
    assert len(reg.info()) >= 1

    new = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id="",
        factors={"Zinc phosphate": 9.0},
        cure_temperature_c=81.0,
        measured={"salt_spray_hours": 750.0},
        source="workbench",
        label="wb:1:new",
    )
    from app.services.workbench_training import _compute_prediction_bias

    bias = _compute_prediction_bias([new], ProductDomain.anticorrosion_coating, "")
    assert bias.get("n_rows") == 1
    assert "salt_spray_hours" in bias["by_metric"]
    assert bias["by_metric"]["salt_spray_hours"]["n"] == 1
    assert "mean_error" in bias["by_metric"]["salt_spray_hours"]


def test_ingest_writes_loop_history_when_bias(tmp_path, monkeypatch):
    from app.db.store import SqlExperimentStore
    from app.services.training import ModelRegistry

    db_path = tmp_path / "bias-history.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store, get_campaign_store

    reset_campaign_store(SqliteCampaignStore(factory))
    store = SqlExperimentStore(factory)
    reg = ModelRegistry(store=store)
    monkeypatch.setattr(training_mod, "registry", reg)

    settings = get_settings()
    n = settings.min_train_samples
    seeds = [
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            project_id="",
            factors={"Zinc phosphate": 8.0 + i},
            cure_temperature_c=80.0,
            measured={"salt_spray_hours": 700.0 + i * 5},
            source="test",
            label=f"seed-{i}",
        )
        for i in range(n)
    ]
    reg.add(seeds, retrain=True)

    from app.domain.schemas import DOEPlan, DOERun

    plan = DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 9.0, "cure_temperature_c": 81.0})],
        notes="test",
        plan_id="bias12345",
        domain=ProductDomain.anticorrosion_coating,
    )
    import asyncio

    cs = get_campaign_store()

    async def _create():
        return await cs.create_from_plan(plan, name="bias-campaign")

    campaign = asyncio.run(_create())
    rows = asyncio.run(cs.list_rows(campaign.id))
    row = rows[0]
    updated, new_rows = asyncio.run(
        cs.batch_sync(
            campaign.id,
            [{"id": row.id, "status": "Completed", "measurements": {"salt_spray_hours": 755.0}, "actual_params": {"Zinc phosphate": 9.0, "cure_temperature_c": 81.0}}],
        )
    )
    assert updated == 1

    from app.services.workbench_training import ingest_workbench_rows

    result = ingest_workbench_rows(campaign.id, new_rows)
    assert result["ingested"] == 1
    assert result.get("prediction_bias")
    assert result["prediction_bias"].get("by_metric")

    camp = asyncio.run(cs.get_campaign(campaign.id))
    history = camp.loop_history or []
    assert any(h.get("type") == "prediction_bias" for h in history)
