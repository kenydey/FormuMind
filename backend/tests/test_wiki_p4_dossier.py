"""P4 Project Dossier — ensure skeleton, data.json sidecar, flag gates."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.services.wiki.dossier import ensure_project_dossier, get_dossier_pack, get_dossier_page
from app.services.wiki.schema import project_dossier_data_path, project_dossier_path
from app.services.wiki.vertical_addendum import list_addenda, resolve_addendum


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_LLM_NARRATIVE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "dossier.db"
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


def _make_project(projects: ProjectStore, *, title: str = "硅烷转化膜项目") -> str:
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        voc_limit_gpl=420,
        notes="冷轧板喷涂前处理",
    )
    detail = projects.create(title=title, requirement=req)
    return detail.id


def test_flag_off_blocks_ensure(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "false")
    get_settings.cache_clear()
    pid = _make_project(env["projects"])
    with pytest.raises(PermissionError):
        ensure_project_dossier(pid)


def test_ensure_writes_md_and_data_json(env):
    pid = _make_project(env["projects"])
    out = ensure_project_dossier(pid, vertical="silane")
    assert out["ok"] is True
    assert out["path"] == project_dossier_path(pid)
    assert out["data_path"] == project_dossier_data_path(pid)

    wiki: WikiStore = env["wiki"]
    md = wiki.read_markdown(out["path"]) or ""
    assert "## S1. 技术要求与目标指标" in md
    assert "## S6. 寻优、闭环与模型态" in md
    assert "salt_spray_hours" in md
    assert "500" in md
    assert f"project_id`=`{pid}`" in md or f"`{pid}`" in md

    data_file = env["root"] / out["data_path"]
    assert data_file.is_file()
    side = json.loads(data_file.read_text(encoding="utf-8"))
    assert side["project_id"] == pid
    assert side["template"] == "project_dossier"
    assert "S1_requirements" in side["section_revisions"]
    assert side["vertical_addendum"] == "silane"
    assert any(r["metric"] == "salt_spray_hours" for r in side["requirements"]["rows"])


def test_pack_endpoint_and_get_page(env):
    pid = _make_project(env["projects"])
    ensure_project_dossier(pid)
    pack = get_dossier_pack(pid)
    assert pack["project_id"] == pid
    assert pack["schema_version"] == 1
    page = get_dossier_page(pid)
    assert page is not None
    assert page["data"]["project_id"] == pid


def test_api_ensure_and_pack(env):
    pid = _make_project(env["projects"])
    client = TestClient(app)
    r = client.post("/api/wiki/dossier/ensure", json={"project_id": pid, "vertical": "silane"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    r2 = client.get(f"/api/wiki/dossier/{pid}/pack")
    assert r2.status_code == 200
    assert r2.json()["project_id"] == pid

    r3 = client.get(f"/api/wiki/dossier/{pid}")
    assert r3.status_code == 200
    assert "S1." in r3.json()["markdown"]


def test_api_409_when_flag_off(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "false")
    get_settings.cache_clear()
    pid = _make_project(env["projects"])
    client = TestClient(app)
    r = client.post("/api/wiki/dossier/ensure", json={"project_id": pid})
    assert r.status_code == 409


def test_vertical_addendum_registry():
    assert "silane" in list_addenda()
    text = resolve_addendum("silane")
    assert "水解" in text or "硅烷" in text
    assert resolve_addendum("") == ""
    assert resolve_addendum("unknown-vertical") == ""
