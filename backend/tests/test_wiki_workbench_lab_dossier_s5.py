"""Workbench Completed rows → SQL ExperimentRow → dossier S5 lab ledger."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import ExperimentRow
from app.db.project_store import ProjectStore
from app.db.store import JsonExperimentStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import (
    DOEFactor,
    DOEPlan,
    DOERun,
    ProductDomain,
    Requirement,
)
from app.main import app
from app.services.training import registry
from app.services.wiki.dossier import ensure_project_dossier, get_dossier_pack, refresh_dossier
from app.services.wiki.schema import project_dossier_path
from app.services.workbench_training import persist_workbench_lab_ledger


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUMIND_WORKBENCH_AUTO_TRAIN", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wb_s5.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    projects = ProjectStore(factory)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    monkeypatch.setattr(ps_mod, "_store", projects)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    reset_campaign_store(SqliteCampaignStore(factory))
    store = JsonExperimentStore(str(tmp_path / "exp.json"))
    registry._store = store  # noqa: SLF001
    registry.load()
    get_settings.cache_clear()
    yield {
        "wiki": wiki,
        "projects": projects,
        "factory": factory,
        "root": wiki_root,
    }
    reset_campaign_store(None)


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[DOEFactor(name="pH", low=3.5, high=5.5, unit="")],
        runs=[DOERun(run_id=1, coded={"pH": 0.0}, natural={"pH": 4.5})],
        notes="workbench→S5",
        plan_id="wb-s5-plan",
        domain=ProductDomain.anticorrosion_coating,
    )


def test_persist_lab_ledger_then_dossier_s5(env):
    projects: ProjectStore = env["projects"]
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=720,
    )
    pid = projects.create(title="Workbench S5 项目", requirement=req).id

    client = TestClient(app)
    created = client.post(
        "/api/experiments/workbench/campaigns",
        json={
            "plan": _plan().model_dump(),
            "project_id": pid,
            "name": "S5 lab ledger campaign",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    campaign_id = body["campaign_id"]
    row = body["rows"][0]

    detail = projects.get(pid)
    assert detail is not None
    assert detail.workspace.workbench_campaign_id == campaign_id

    sync = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [
                {
                    "id": row["id"],
                    "status": "Pending",
                    "actual_params": {"pH": 4.5},
                    "measurements": {"salt_spray_hours": 680.0},
                }
            ],
        },
    )
    assert sync.status_code == 200, sync.text
    sync_body = sync.json()
    assert sync_body["training_ingested"] >= 1 or (
        (sync_body.get("training_message") or "").find("台账") >= 0
    )

    with env["factory"]() as session:
        exps = (
            session.query(ExperimentRow)
            .filter(ExperimentRow.project_id == pid)
            .all()
        )
        assert exps, "expected SQL ExperimentRow for dossier S5"
        assert any(
            (e.measured or {}).get("salt_spray_hours") == 680.0 for e in exps
        )
        assert all(e.source == "workbench" or e.label.startswith("wb:") for e in exps)

    ensure_project_dossier(pid)
    pack = get_dossier_pack(pid)
    assert pack["flags"]["empty_lab"] is False
    lab_rows = (pack.get("lab") or {}).get("rows") or []
    assert any(r.get("metric") == "salt_spray_hours" for r in lab_rows)
    assert any(
        str(r.get("source") or "").startswith("experiment:")
        or str(r.get("source") or "").startswith("workbench")
        for r in lab_rows
    )
    assert not any(r.get("source") == "workspace.measured" for r in lab_rows)

    refresh_dossier(pid)
    md = env["wiki"].read_markdown(project_dossier_path(pid)) or ""
    assert "680" in md
    assert "S5" in md or "实验台账" in md


def test_lab_ledger_survives_registry_failure(env, monkeypatch):
    projects: ProjectStore = env["projects"]
    pid = projects.create(
        title="S5 registry fail",
        requirement=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            salt_spray_hours=500,
        ),
    ).id

    client = TestClient(app)
    created = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump(), "project_id": pid},
    ).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]

    def boom(*_a, **_k):
        raise RuntimeError("datalab down")

    monkeypatch.setattr(registry, "add", boom)

    sync = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [
                {
                    "id": row["id"],
                    "status": "Pending",
                    "actual_params": {"pH": 4.2},
                    "measurements": {"salt_spray_hours": 640.0},
                }
            ],
        },
    )
    assert sync.status_code == 200, sync.text
    # Sync must not 500; ledger still written
    pack = get_dossier_pack(pid)
    assert pack["flags"]["empty_lab"] is False
    assert any(
        r.get("metric") == "salt_spray_hours" and float(r.get("value") or 0) == 640.0
        for r in (pack.get("lab") or {}).get("rows") or []
    )


def test_persist_lab_ledger_idempotent(env):
    projects: ProjectStore = env["projects"]
    pid = projects.create(
        title="S5 idem",
        requirement=Requirement(domain=ProductDomain.anticorrosion_coating),
    ).id
    client = TestClient(app)
    created = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump(), "project_id": pid},
    ).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]
    client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [
                {
                    "id": row["id"],
                    "status": "Pending",
                    "actual_params": {"pH": 4.5},
                    "measurements": {"salt_spray_hours": 700.0},
                }
            ],
        },
    )
    out1 = persist_workbench_lab_ledger(campaign_id)
    out2 = persist_workbench_lab_ledger(campaign_id)
    assert out1["upserted"] >= 1
    assert out2["upserted"] >= 1
    with env["factory"]() as session:
        n = (
            session.query(ExperimentRow)
            .filter(ExperimentRow.project_id == pid)
            .count()
        )
    assert n == 1
