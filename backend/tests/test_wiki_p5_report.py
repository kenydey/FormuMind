"""P5 Report generation from DossierPack."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.wiki.report import generate_report, list_report_templates, render_report_markdown
from app.services.wiki.schema import project_report_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "report.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    projects = ProjectStore(factory)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    monkeypatch.setattr(ps_mod, "_store", projects)
    get_settings.cache_clear()
    return {"wiki": wiki, "projects": projects, "root": wiki_root}


def _make_project(projects: ProjectStore) -> str:
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        voc_limit_gpl=420,
    )
    return projects.create(title="报告测试项目", requirement=req).id


def test_list_templates():
    ids = {t["id"] for t in list_report_templates()}
    assert ids == {"briefing", "feasibility", "formula-compare", "patent-memo"}


def test_generate_briefing_persists(env):
    pid = _make_project(env["projects"])
    out = generate_report(pid, "briefing", prompt="关注盐雾与 VOC", persist=True)
    assert out["ok"] is True
    assert out["path"] == project_report_path(pid, "briefing")
    assert out["disclaimer"] == "draft_not_claims"
    md = out["markdown"]
    assert "盐雾" in md or "salt_spray" in md or "500" in md
    assert "source_ids" in md
    assert "不得" in md or "draft" in md.lower() or "Claims" in md
    assert env["wiki"].get_by_path(out["path"]) is not None
    assert "报告测试项目" in (env["wiki"].read_markdown(out["path"]) or "")


def test_feasibility_and_patent_templates(env):
    pid = _make_project(env["projects"])
    fe = generate_report(pid, "feasibility", persist=False)
    assert "基准配方" in fe["markdown"] or "DOE" in fe["markdown"]
    pm = generate_report(pid, "patent-memo", persist=False)
    assert "文献" in pm["markdown"] or "来源" in pm["markdown"]


def test_flag_gates_report(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_REPORT_ENABLED", "false")
    get_settings.cache_clear()
    pid = _make_project(env["projects"])
    with pytest.raises(PermissionError):
        generate_report(pid, "briefing")


def test_api_generate_and_templates(env):
    pid = _make_project(env["projects"])
    client = TestClient(app)
    r = client.get("/api/wiki/reports/templates")
    assert r.status_code == 200
    assert any(t["id"] == "briefing" for t in r.json()["templates"])

    r2 = client.post(
        "/api/wiki/dossier/report",
        json={"project_id": pid, "template": "briefing", "prompt": "强调 VOC"},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["ok"] is True
    assert "VOC" in body["markdown"] or "voc" in body["markdown"].lower() or "提示" in body["markdown"]

    r3 = client.post("/api/wiki/dossier/report", json={"project_id": pid, "template": "deck"})
    assert r3.status_code == 400


def test_render_citations_never_claim_l2():
    pack = {
        "project_id": "p1",
        "title": "t",
        "domain": "x",
        "updated_at": "now",
        "requirements": {"rows": [{"metric": "a", "value": 1, "unit": "", "direction": "", "source": "Requirement"}]},
        "literature": {"rows": [], "source_ids": ["src-1"]},
        "loop": {"history": [], "candidates": []},
        "flags": {"empty_lab": True},
    }
    md = render_report_markdown(template="briefing", pack=pack)
    assert "`src-1`" in md
    assert "非 Claims" in md or "不得" in md
