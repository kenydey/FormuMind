"""Batch B: project-level auto_loop_on_sync OR + campaign_loop_status + converge stop."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, get_campaign_store, reset_campaign_store
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.store import JsonExperimentStore
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement
from app.main import app
from app.services import workbench_loop
from app.services.training import registry


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0, "cure_temperature_c": 80.0}),
        ],
        notes="test",
        plan_id="projloop1",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUMIND_WORKBENCH_AUTO_TRAIN", "true")
    monkeypatch.setenv("FORMUMIND_AUTO_LOOP_ON_SYNC", "false")
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    db_path = tmp_path / "proj_loop.db"
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    store = JsonExperimentStore(str(tmp_path / "exp.json"))
    registry._store = store  # noqa: SLF001
    registry.load()

    import app.db.project_store as ps_mod

    projects = ProjectStore(factory)
    monkeypatch.setattr(ps_mod, "_store", projects)

    yield {"projects": projects, "factory": factory}
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_should_trigger_respects_project_or(monkeypatch):
    assert not workbench_loop.should_trigger_loop_after_sync(
        1, trigger_loop=None, project_id=None
    )

    monkeypatch.setattr(
        workbench_loop,
        "_project_auto_loop_on",
        lambda pid: pid == "p-on",
    )
    assert workbench_loop.should_trigger_loop_after_sync(
        1, trigger_loop=None, project_id="p-on"
    )
    assert not workbench_loop.should_trigger_loop_after_sync(
        1, trigger_loop=None, project_id="p-off"
    )
    assert not workbench_loop.should_trigger_loop_after_sync(
        1, trigger_loop=False, project_id="p-on"
    )


def test_campaign_loop_status_converged_stops_dispatch(_sqlite_backend):
    client = TestClient(app)
    created = client.post(
        "/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()}
    ).json()
    cid = created["campaign_id"]
    st = workbench_loop.campaign_loop_status(cid)
    assert st["status"] == "idle"
    assert st["rounds"] == 0

    store = get_campaign_store()
    store.append_loop_history_sync(
        cid,
        {
            "round": 1,
            "rmse_by_metric": {"salt_spray_hours": 0.05},
            "converged": True,
            "loop_message": "闭环已收敛，建议停止迭代",
            "doe_plan_id": "doe-x",
        },
    )
    st2 = workbench_loop.campaign_loop_status(cid)
    assert st2["status"] == "converged"
    assert st2["converged"] is True
    assert st2["rounds"] == 1

    task_id, msg = workbench_loop.dispatch_loop_after_sync(
        training_ingested=1,
        workbench_campaign_id=cid,
        trigger_loop=True,
    )
    assert task_id is None
    assert "收敛" in msg


def test_project_workspace_auto_loop_triggers_without_explicit_flag(_sqlite_backend):
    projects: ProjectStore = _sqlite_backend["projects"]
    detail = projects.create(
        title="proj-auto-loop",
        requirement=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            substrate="carbon_steel",
            salt_spray_hours=720,
        ),
    )
    pid = detail.id
    ws = detail.workspace.model_copy(update={"auto_loop_on_sync": True})
    projects.update(pid, ws.model_dump(mode="json"))

    client = TestClient(app)
    created = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump(), "project_id": pid},
    ).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]

    sync = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [
                {
                    "id": row["id"],
                    "status": "Completed",
                    "actual_params": {"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
                    "measurements": {"salt_spray_hours": 860.0},
                }
            ],
        },
    )
    assert sync.status_code == 200, sync.text
    body = sync.json()
    assert body["training_ingested"] == 1
    assert body.get("loop_task_id"), body
    assert body.get("loop_status") is not None
