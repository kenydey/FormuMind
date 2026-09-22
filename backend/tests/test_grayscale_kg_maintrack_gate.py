"""Main-track gate: wiki grayscale dossier/report + KG feedback observability.

In-process only (no live Hub / ELN). Companion:
docs/plans/2026-09-22-grayscale-kg-maintrack.md
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.wiki.dossier import ensure_project_dossier, refresh_dossier


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.entity_store as es_mod
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_mod

    db_path = tmp_path / "maintrack.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    projects = ProjectStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    entities = EntityStore(factory)
    monkeypatch.setattr(ps_mod, "_store", projects)
    monkeypatch.setattr(wiki_mod, "_store", wiki)
    monkeypatch.setattr(es_mod, "_store", entities)
    get_settings.cache_clear()
    return {
        "projects": projects,
        "wiki": wiki,
        "entities": entities,
        "factory": factory,
    }


def test_grayscale_dossier_report_and_kg_stats(env):
    """Gray flags: ensure/refresh/pack + briefing export MD; KG stats reachable."""
    projects: ProjectStore = env["projects"]
    detail = projects.create(
        title="maintrack-gray",
        requirement=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            substrate="carbon_steel",
            salt_spray_hours=720,
            voc_limit_gpl=350,
        ),
    )
    pid = detail.id

    ensure_project_dossier(pid, vertical="silane")
    refresh_dossier(pid)

    client = TestClient(app)
    pack = client.get(f"/api/wiki/dossier/{pid}/pack")
    assert pack.status_code == 200
    body = pack.json()
    assert body.get("requirements", {}).get("rows"), "S1 should hydrate from requirement"
    assert body.get("flags", {}).get("missing_requirement") is False

    gen = client.post(
        "/api/wiki/dossier/report",
        json={
            "project_id": pid,
            "template": "briefing",
            "ensure_dossier": True,
            "persist": True,
            "use_llm": False,
        },
    )
    assert gen.status_code == 200
    assert gen.json().get("disclaimer") == "draft_not_claims"

    export = client.post(
        "/api/wiki/dossier/report/export",
        json={
            "project_id": pid,
            "template": "briefing",
            "format": "md",
            "ensure_dossier": True,
            "use_llm": False,
        },
    )
    assert export.status_code == 200
    text = export.content.decode("utf-8", errors="replace")
    assert "720" in text or "salt_spray" in text or "简报" in text

    stats = client.get("/api/kg/feedback/stats")
    assert stats.status_code == 200
    st = stats.json()
    assert "measured_performance" in st or "measured_material" in st or "measured" in str(st)
    # Shape lock used by Hub KgRelationPanel
    assert "measured_material" in st
    assert "measured_domain" in st
    assert isinstance(st["measured_material"], int)
    assert isinstance(st["measured_domain"], int)


def test_kg_measured_link_visible_in_stats(env):
    entities: EntityStore = env["entities"]
    with entities._session_factory() as s:
        entities.upsert_entity(
            s, id="mat:gptms", canonical_name="GPTMS", kind="chemical"
        )
        entities.upsert_entity(
            s, id="prop:salt_spray_hours", canonical_name="salt_spray_hours", kind="property"
        )
    with entities._session_factory() as s:
        entities.merge_semantic_link(
            s,
            src_entity_id="mat:gptms",
            dst_entity_id="prop:salt_spray_hours",
            link_type="measured_performance",
            confidence=0.85,
            evidence_ref={
                "source_id": "measured:campaign_maintrack",
                "extraction_method": "measured",
                "sentence": "实测 salt_spray_hours=680",
                "metric": "salt_spray_hours",
                "measured_value": 680.0,
            },
            extraction_method="measured",
        )

    client = TestClient(app)
    st = client.get("/api/kg/feedback/stats").json()
    assert st["measured_material"] >= 1
