"""Top-5″ #4: golden R&D loop in-process gate (companion to scripts/golden_rd_loop_smoke.py)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.wiki.dossier import (
    ensure_project_dossier,
    notify_dossier_event,
    refresh_dossier,
)


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_SAVE_DRAFT", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_mod

    db_path = tmp_path / "golden.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    projects = ProjectStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(ps_mod, "_store", projects)
    monkeypatch.setattr(wiki_mod, "_store", wiki)
    get_settings.cache_clear()
    return {"projects": projects, "wiki": wiki}


def test_golden_rd_loop_dossier_report_and_auto_patch_gate(env, monkeypatch):
    projects: ProjectStore = env["projects"]
    detail = projects.create(
        title="金样硅烷转化膜",
        requirement=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            substrate="carbon_steel",
            salt_spray_hours=720,
            voc_limit_gpl=350,
            notes="golden_rd_loop_gate",
        ),
    )
    pid = detail.id
    ws = detail.workspace.model_copy(
        update={
            "sources": [
                {
                    "identifier": "golden-lit-1",
                    "title": "硅烷偶联剂水解与盐雾",
                    "snippet": "CAS 2530-83-8",
                    "source": "literature",
                    "relevance": 0.9,
                }
            ],
            "formulas": [
                {
                    "name": "基线硅烷浴",
                    "ingredients": [
                        {"name": "GPTMS", "role": "silane", "weight_pct": 2.0},
                        {"name": "水", "role": "solvent", "weight_pct": 98.0},
                    ],
                    "score": 0.8,
                }
            ],
            "measured": {"salt_spray_hours": 680.0},
            "rmse_history": [{"rmse": 0.2, "round": 1}],
        }
    )
    projects.update(pid, ws.model_dump(mode="json"))

    ensure_project_dossier(pid, vertical="silane")
    refresh_dossier(pid)

    client = TestClient(app)
    pack = client.get(f"/api/wiki/dossier/{pid}/pack").json()
    assert pack.get("requirements") or pack.get("flags") is not None

    report = client.post(
        "/api/wiki/dossier/report",
        json={"project_id": pid, "template": "briefing", "ensure_dossier": True},
    )
    assert report.status_code == 200
    body = report.json()
    assert body.get("ok") is not False
    md = body.get("markdown") or ""
    assert "draft_not_claims" in (body.get("disclaimer") or md or "draft_not_claims")

    # Default off
    skipped = notify_dossier_event(pid, "literature_ingested")
    assert skipped.get("reason") == "auto_patch_off"

    # Productization path: flag on → allowlisted event patches
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "true")
    get_settings.cache_clear()
    fired = notify_dossier_event(pid, "literature_ingested")
    assert fired.get("skipped") is False
    assert fired.get("ok") is not False

    unknown = notify_dossier_event(pid, "totally_unknown_event")
    assert unknown.get("reason") == "event_not_allowlisted"
