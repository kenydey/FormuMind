"""Top-5‴ #4: project-level wiki_dossier_auto_patch (global still off)."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.project_store import ProjectStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductDomain, Requirement
from app.services.wiki.dossier import ensure_project_dossier, notify_dossier_event


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PROJECT_DOSSIER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOSSIER_AUTO_PATCH", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_mod

    db_path = tmp_path / "proj_ap.db"
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


def test_project_level_auto_patch_overrides_global_off(env):
    projects: ProjectStore = env["projects"]
    detail = projects.create(
        title="proj-auto-patch",
        requirement=Requirement(
            domain=ProductDomain.anticorrosion_coating,
            substrate="carbon_steel",
            salt_spray_hours=720,
        ),
    )
    pid = detail.id
    ensure_project_dossier(pid)

    skipped = notify_dossier_event(pid, "literature_ingested")
    assert skipped.get("reason") == "auto_patch_off"

    ws = detail.workspace.model_copy(update={"wiki_dossier_auto_patch": True})
    projects.update(pid, ws.model_dump(mode="json"))

    fired = notify_dossier_event(pid, "literature_ingested")
    assert fired.get("skipped") is False
    assert fired.get("auto_patch_scope") == "project"
