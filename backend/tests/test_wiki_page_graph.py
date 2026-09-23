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


def test_graph_insights_orphans_broken_and_components(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="See [[B]]")
    _upsert(wiki, path="materials/b.md", kind="material", title="B", body_extra="[[ghost-x]]")
    _upsert(wiki, path="materials/lonely.md", kind="material", title="Lonely")
    _upsert(
        wiki,
        path="themes/project-demo.md",
        kind="theme",
        title="Dossier",
        body_extra="[[A]]",
    )
    _upsert(wiki, path="reports/project-demo-brief.md", kind="report", title="Brief")

    client = TestClient(app)
    r = client.get("/api/wiki/graph", params={"limit": 100})
    assert r.status_code == 200
    body = r.json()
    insights = body["insights"]
    orphan_paths = {o["path"] for o in insights["orphans"]}
    # Lonely has degree_in=0 and is not a structure skip → lint-aligned orphan
    assert "materials/lonely.md" in orphan_paths
    # Structure skips must not appear as lint orphans
    assert "themes/project-demo.md" not in orphan_paths
    assert "reports/project-demo-brief.md" not in orphan_paths
    # A is linked from dossier → not orphan; B has inbound from A
    assert "materials/a.md" not in orphan_paths
    assert "materials/b.md" not in orphan_paths

    broken_targets = {b["target"] for b in insights["broken"]}
    assert "ghost-x" in broken_targets
    assert body["meta"]["broken_links"] >= 1
    assert body["meta"]["orphan_count"] >= 1
    assert insights["components"]["count"] >= 1
    assert insights["components"]["largest"] >= 1

    isolate_paths = {i["path"] for i in insights["isolates"]}
    assert "materials/lonely.md" in isolate_paths
    # Brief report has no links → isolate, but not lint orphan
    assert "reports/project-demo-brief.md" in isolate_paths


def test_shared_neighbor_weight_and_community(wiki_env):
    """Triangle A↔B↔C↔A raises shared-neighbor weight; orphan alone = own community."""
    wiki = wiki_env
    # Mutual links: A-B, B-C, C-A → each edge has one shared neighbor
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="[[B]] [[C]]")
    _upsert(wiki, path="materials/b.md", kind="material", title="B", body_extra="[[A]] [[C]]")
    _upsert(wiki, path="materials/c.md", kind="material", title="C", body_extra="[[A]] [[B]]")
    _upsert(wiki, path="materials/lonely.md", kind="material", title="Lonely")

    client = TestClient(app)
    r = client.get("/api/wiki/graph", params={"limit": 100, "include_orphan": True})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["weighting"] == "shared_neighbors"
    assert body["meta"]["community_count"] >= 2

    by_path = {n["path"]: n for n in body["nodes"]}
    assert "community" in by_path["materials/a.md"]
    # Triangle members share one community; lonely is another
    ca = by_path["materials/a.md"]["community"]
    cb = by_path["materials/b.md"]["community"]
    cc = by_path["materials/c.md"]["community"]
    cl = by_path["materials/lonely.md"]["community"]
    assert ca == cb == cc
    assert cl != ca

    weights = {(e["source"], e["target"]): e["weight"] for e in body["edges"]}
    # Directed edges present; shared neighbor → weight > 1
    w_ab = weights.get(("materials/a.md", "materials/b.md")) or weights.get(
        ("materials/b.md", "materials/a.md")
    )
    assert w_ab is not None and w_ab > 1.0
