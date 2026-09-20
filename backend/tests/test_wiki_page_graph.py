"""P0: Wiki page [[wikilink]] graph API (not materials KG)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_PAGE_GRAPH_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_graph.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return wiki


def _upsert(wiki: WikiStore, *, path: str, kind: str, title: str, body_extra: str = ""):
    key = path.rsplit("/", 1)[-1].replace(".md", "")
    md = dump_page(
        kind=kind,
        title=title,
        entity_id=f"{kind}:{key}",
        norm_key=key,
        source_ids=["s1"],
        summary=f"{title} summary.",
    )
    if body_extra:
        md = md.rstrip() + "\n\n## Links\n" + body_extra + "\n"
    wiki.upsert_page(
        path=path,
        kind=kind,
        title=title,
        norm_key=key,
        entity_id=f"{kind}:{key}",
        markdown=md,
        source_ids=["s1"],
    )


def test_graph_flag_off_returns_409(wiki_env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_PAGE_GRAPH_ENABLED", "false")
    get_settings.cache_clear()
    client = TestClient(app)
    r = client.get("/api/wiki/graph")
    assert r.status_code == 409
    assert "wiki_page_graph_enabled" in r.json()["detail"]


def test_graph_chain_a_b_c_and_broken_link(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="See [[B]]")
    _upsert(wiki, path="materials/b.md", kind="material", title="B", body_extra="See [[C]] and [[missing-ghost]]")
    _upsert(wiki, path="materials/c.md", kind="material", title="C", body_extra="Leaf")
    _upsert(wiki, path="materials/orphan.md", kind="material", title="Orphan Alone")

    client = TestClient(app)
    r = client.get("/api/wiki/graph", params={"limit": 100, "include_orphan": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    paths = {n["path"] for n in body["nodes"]}
    assert paths >= {"materials/a.md", "materials/b.md", "materials/c.md", "materials/orphan.md"}
    edge_set = {(e["source"], e["target"]) for e in body["edges"]}
    assert ("materials/a.md", "materials/b.md") in edge_set
    assert ("materials/b.md", "materials/c.md") in edge_set
    # Broken [[missing-ghost]] must not create a node
    assert not any("missing-ghost" in (n["path"] or "") for n in body["nodes"])
    assert body["meta"]["broken_links"] >= 1
    by_path = {n["path"]: n for n in body["nodes"]}
    assert by_path["materials/b.md"]["degree_in"] == 1
    assert by_path["materials/b.md"]["degree_out"] == 1
    assert by_path["materials/orphan.md"]["degree"] == 0


def test_graph_hide_orphan_and_limit(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="[[B]]")
    _upsert(wiki, path="materials/b.md", kind="material", title="B")
    _upsert(wiki, path="materials/lonely.md", kind="material", title="Lonely")

    client = TestClient(app)
    r = client.get("/api/wiki/graph", params={"include_orphan": False})
    assert r.status_code == 200
    paths = {n["path"] for n in r.json()["nodes"]}
    assert "materials/lonely.md" not in paths
    assert "materials/a.md" in paths
    assert "materials/b.md" in paths


def test_graph_kind_filter(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="[[Theme One]]")
    _upsert(wiki, path="themes/t1.md", kind="theme", title="Theme One")

    client = TestClient(app)
    r = client.get("/api/wiki/graph", params={"kinds": "material"})
    assert r.status_code == 200
    paths = {n["path"] for n in r.json()["nodes"]}
    assert paths == {"materials/a.md"}
    # Theme target filtered out → counted toward broken/skip
    assert r.json()["meta"]["broken_links"] >= 1
