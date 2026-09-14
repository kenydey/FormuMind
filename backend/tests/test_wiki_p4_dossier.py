"""P4 Project Dossier — ensure, hydrate, patch, refresh, auto_patch gates."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import DOEFactor, DOEPlan, DOERun, ProductDomain, Requirement
from app.main import app
from app.services.wiki.dossier import (
    ensure_project_dossier,
    get_dossier_pack,
    get_dossier_page,
    notify_dossier_event,
    patch_dossier_sections,
    refresh_dossier,
)
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
    return {"wiki": wiki, "projects": projects, "root": wiki_root, "factory": factory}


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


def test_pack_hydrates_doe_from_workspace(env):
    pid = _make_project(env["projects"])
    detail = env["projects"].get(pid)
    assert detail is not None
    plan = DOEPlan(
        plan_id="doe-test-1",
        design="full_factorial",
        factors=[DOEFactor(name="pH", low=3.0, high=5.0, unit="")],
        runs=[DOERun(run_id=1, coded={"pH": 0.0}, natural={"pH": 4.0})],
        notes="unit-test plan",
    )
    ws = detail.workspace.model_copy(update={"doe_plan": plan, "rmse_history": [{"rmse": 0.12}]})
    env["projects"].update(pid, ws.model_dump(mode="json"))

    pack = get_dossier_pack(pid)
    assert pack["doe"]["plans"], "expected hydrated DOE plans"
    assert pack["doe"]["plans"][0]["design_type"] == "full_factorial"
    assert pack["doe"]["runs"], "expected hydrated DOE runs"
    assert pack["loop"]["history"], "expected loop history from rmse_history"
    assert pack["flags"]["empty_doe"] is False

    # Ensure + patch S4 should render design into markdown
    ensure_project_dossier(pid)
    out = patch_dossier_sections(pid, ["S4", "S6"])
    assert out["ok"] is True
    md = env["wiki"].read_markdown(project_dossier_path(pid)) or ""
    assert "full_factorial" in md
    assert "0.12" in md


def test_patch_isolates_sections(env):
    pid = _make_project(env["projects"])
    ensure_project_dossier(pid)
    side0 = json.loads((env["root"] / project_dossier_data_path(pid)).read_text(encoding="utf-8"))
    s1_rev = side0["section_revisions"]["S1_requirements"]
    s8_rev = side0["section_revisions"]["S8_open_questions"]

    # Change requirement → S1 content should change on patch S1 only
    detail = env["projects"].get(pid)
    req = detail.workspace.requirement.model_copy(update={"salt_spray_hours": 720})
    ws = detail.workspace.model_copy(update={"requirement": req})
    env["projects"].update(pid, ws.model_dump(mode="json"), title=detail.title)

    out = patch_dossier_sections(pid, ["S1"])
    assert "S1_requirements" in out["patched_sections"]
    assert "S8_open_questions" not in out["patched_sections"]

    side1 = json.loads((env["root"] / project_dossier_data_path(pid)).read_text(encoding="utf-8"))
    assert side1["section_revisions"]["S1_requirements"] == s1_rev + 1
    assert side1["section_revisions"]["S8_open_questions"] == s8_rev

    md = env["wiki"].read_markdown(project_dossier_path(pid)) or ""
    assert "720" in md
    assert "## S8. 开放问题 / Flag / 下一步" in md

    # Idempotent second patch of S1 with no further change
    out2 = patch_dossier_sections(pid, ["S1"])
    assert out2["patched_sections"] == []
    assert "S1_requirements" in out2["skipped_unchanged"]


def test_refresh_and_auto_patch_gate(env, monkeypatch):
    pid = _make_project(env["projects"])
    out = refresh_dossier(pid, sections=["S2", "S8"])
    assert out["ok"] is True
    assert project_dossier_path(pid)

    skipped = notify_dossier_event(pid, "project_updated")
    assert skipped.get("skipped") is True
    assert skipped.get("reason") == "auto_patch_off"

    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "true")
    get_settings.cache_clear()
    detail = env["projects"].get(pid)
    req = detail.workspace.requirement.model_copy(update={"voc_limit_gpl": 350})
    ws = detail.workspace.model_copy(update={"requirement": req})
    env["projects"].update(pid, ws.model_dump(mode="json"))

    fired = notify_dossier_event(pid, "project_updated")
    assert fired.get("ok") is True
    assert fired.get("skipped") is False
    assert "S1_requirements" in (fired.get("patched_sections") or fired.get("section_revisions") or {})


def test_pack_endpoint_and_get_page(env):
    pid = _make_project(env["projects"])
    ensure_project_dossier(pid)
    pack = get_dossier_pack(pid)
    assert pack["project_id"] == pid
    assert pack["schema_version"] == 1
    page = get_dossier_page(pid)
    assert page is not None
    assert page["data"]["project_id"] == pid


def test_api_ensure_patch_refresh(env):
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

    r4 = client.post("/api/wiki/dossier/patch", json={"project_id": pid, "sections": ["S8"]})
    assert r4.status_code == 200, r4.text
    assert r4.json()["ok"] is True

    r5 = client.post("/api/wiki/dossier/refresh", json={"project_id": pid})
    assert r5.status_code == 200, r5.text
    assert r5.json()["ok"] is True


def test_api_409_when_flag_off(env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "false")
    get_settings.cache_clear()
    pid = _make_project(env["projects"])
    client = TestClient(app)
    r = client.post("/api/wiki/dossier/ensure", json={"project_id": pid})
    assert r.status_code == 409
    r2 = client.post("/api/wiki/dossier/patch", json={"project_id": pid})
    assert r2.status_code == 409


def test_vertical_addendum_registry():
    assert "silane" in list_addenda()
    text = resolve_addendum("silane")
    assert "水解" in text or "硅烷" in text
    assert resolve_addendum("") == ""
    assert resolve_addendum("unknown-vertical") == ""
