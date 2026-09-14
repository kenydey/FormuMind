"""E: Grayscale gate — dossier/report flags on + Claims/DOE isolation + lint actions.

Runs fully in-process (TestClient); no live Hub stack required.
Companion checklist: docs/plans/2026-09-14-wiki-hub-dossier-handtest.md
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.wiki.constraints import wiki_parameter_bounds
from app.services.wiki.dossier import ensure_project_dossier, project_dossier_path, refresh_dossier
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as project_store_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "gray.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    projects = ProjectStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(project_store_mod, "_store", projects)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return {"projects": projects, "wiki": wiki, "root": wiki_root}


def _make_project(projects: ProjectStore) -> str:
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        voc_limit_gpl=420,
        notes="grayscale gate",
    )
    detail = projects.create(title="gray-proj", requirement=req)
    return detail.id


def test_grayscale_dossier_report_and_claims_doe(env):
    pid = _make_project(env["projects"])
    env["wiki"].upsert_page(
        path="systems/catalyst_wt.md",
        kind="system",
        title="catalyst_wt",
        norm_key="catalyst_wt",
        entity_id="system:catalyst_wt",
        markdown=dump_page(
            kind="system",
            title="catalyst_wt",
            entity_id="system:catalyst_wt",
            norm_key="catalyst_wt",
            source_ids=["s-l1"],
            summary="L1 window",
            bounds=[{"name": "catalyst_wt", "min": 0.2, "max": 1.0, "unit": "wt%"}],
        ),
        source_ids=["s-l1"],
    )

    out = ensure_project_dossier(pid)
    assert out.get("ok") is True
    path = project_dossier_path(pid)
    assert path.startswith("themes/project-")

    md = env["wiki"].read_markdown(path) or ""
    poisoned = md.replace(
        "---\n",
        "---\nbounds_json: '[{\"name\":\"poison_l2\",\"min\":9,\"max\":1}]'\n",
        1,
    )
    row = env["wiki"].get_by_path(path)
    assert row is not None
    env["wiki"].upsert_page(
        path=path,
        kind=row.kind,
        title=row.title or "",
        norm_key=row.norm_key or "",
        entity_id=row.entity_id,
        markdown=poisoned,
        source_ids=list(row.source_ids or []),
        flags=list(row.flags or []),
    )

    refresh = refresh_dossier(pid)
    assert refresh.get("ok") is True

    bounds = wiki_parameter_bounds()
    names = {str(b.get("name") or "").lower() for b in bounds}
    assert "poison_l2" not in names
    assert "catalyst_wt" in names
    paths = [str(b.get("path") or "") for b in bounds]
    assert not any(p.startswith("themes/project-") for p in paths)
    assert not any(p.startswith("reports/") for p in paths)

    client = TestClient(app)
    r = client.get(f"/api/wiki/dossier/{pid}")
    assert r.status_code == 200
    assert "themes/project-" in (r.json().get("path") or "")

    pack = client.get(f"/api/wiki/dossier/{pid}/pack")
    assert pack.status_code == 200

    report = client.post(
        "/api/wiki/dossier/report",
        json={"project_id": pid, "template": "briefing", "use_llm": False, "persist": True},
    )
    assert report.status_code == 200
    body = report.json()
    assert body.get("ok") is True
    assert "draft_not_claims" in (body.get("disclaimer") or "")
    rpath = body.get("path") or ""
    assert rpath.startswith("reports/")

    lint = client.post("/api/wiki/lint/run", json={"limit": 100, "detect_orphan": True})
    assert lint.status_code == 200
    assert lint.json().get("ok") is True

    flags = client.get("/api/wiki/flags?limit=100")
    assert flags.status_code == 200
    pages = flags.json().get("pages") or []
    for p in pages:
        assert isinstance(p.get("actions"), list)
        assert any(a.get("id") == "open_page" for a in p["actions"])
