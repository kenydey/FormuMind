"""Knowledge Hub project isolation — sources + wiki browse are project-strict."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.project_scope import filter_wiki_rows, wiki_page_in_project
from app.services.wiki.schema import dump_page, project_dossier_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.source_store as source_store_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "scope.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    sources = SourceStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(source_store_mod, "_store", sources)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return {"sources": sources, "wiki": wiki}


def test_list_for_project_strict_excludes_global_and_other(env):
    src = env["sources"]
    a = src.create(
        filename="a.pdf",
        title="proj-a",
        source_kind="pdf",
        full_text="a",
        project_id="proj-a",
        content_hash="h-a",
    )
    src.create(
        filename="b.pdf",
        title="proj-b",
        source_kind="pdf",
        full_text="b",
        project_id="proj-b",
        content_hash="h-b",
    )
    src.create(
        filename="g.pdf",
        title="global",
        source_kind="pdf",
        full_text="g",
        project_id=None,
        content_hash="h-g",
    )

    strict = src.list_for_project("proj-a", include_global=False)
    assert [r.id for r in strict] == [a]
    soft = src.list_for_project("proj-a", include_global=True)
    ids = {r.id for r in soft}
    assert a in ids
    assert any(r.project_id is None for r in soft)


def test_kb_sources_api_default_strict(env):
    src = env["sources"]
    mine = src.create(
        filename="mine.pdf",
        title="mine",
        source_kind="pdf",
        full_text="mine",
        project_id="p1",
        content_hash="hm",
    )
    src.create(
        filename="g.pdf",
        title="global",
        source_kind="pdf",
        full_text="global",
        project_id=None,
        content_hash="hg",
    )
    client = TestClient(app)
    r = client.get("/api/kb/sources", params={"project_id": "p1"})
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["sources"]]
    assert ids == [mine]

    soft = client.get(
        "/api/kb/sources",
        params={"project_id": "p1", "include_global": True},
    )
    soft_ids = {s["id"] for s in soft.json()["sources"]}
    assert mine in soft_ids
    assert len(soft_ids) >= 2


def test_wiki_pages_api_project_scope(env):
    src = env["sources"]
    wiki = env["wiki"]
    sid = src.create(
        filename="s.pdf",
        title="owned",
        source_kind="pdf",
        full_text="owned",
        project_id="proj-x",
        content_hash="hs",
    )
    wiki.upsert_page(
        path="materials/owned.md",
        kind="material",
        title="owned",
        norm_key="owned",
        entity_id="material:owned",
        markdown=dump_page(
            kind="material",
            title="owned",
            entity_id="material:owned",
            norm_key="owned",
            source_ids=[sid],
            summary="from project",
        ),
        source_ids=[sid],
    )
    wiki.upsert_page(
        path="materials/other.md",
        kind="material",
        title="other",
        norm_key="other",
        entity_id="material:other",
        markdown=dump_page(
            kind="material",
            title="other",
            entity_id="material:other",
            norm_key="other",
            source_ids=["foreign"],
            summary="other project",
        ),
        source_ids=["foreign"],
    )
    dpath = project_dossier_path("proj-x")
    wiki.upsert_page(
        path=dpath,
        kind="theme",
        title="dossier",
        norm_key="proj-x",
        entity_id=None,
        markdown="# dossier\n",
        source_ids=[],
    )

    assert wiki_page_in_project(
        path="materials/owned.md",
        page_source_ids=[sid],
        project_id="proj-x",
    )
    assert not wiki_page_in_project(
        path="materials/other.md",
        page_source_ids=["foreign"],
        project_id="proj-x",
    )
    assert filter_wiki_rows(wiki.list_pages(limit=50), "proj-x")

    client = TestClient(app)
    r = client.get("/api/wiki/pages", params={"project_id": "proj-x", "limit": 50})
    assert r.status_code == 200
    paths = {p["path"] for p in r.json()["pages"]}
    assert "materials/owned.md" in paths
    assert dpath in paths
    assert "materials/other.md" not in paths
