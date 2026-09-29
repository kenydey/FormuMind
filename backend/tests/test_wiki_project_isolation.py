"""P0-2: wiki page project isolation.

- ``upsert_page`` stamps ``project_id``; legacy NULL rows are adopted on
  update; cross-project path collisions raise (fail-closed).
- ``list_pages`` / ``search_wiki`` / ``blend_wiki_evidence`` /
  ``search_wiki_embedded`` filter by ``project_id`` so chat never blends
  another project's compiled knowledge.
"""
from __future__ import annotations

import logging

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiProjectCollisionError, WikiStore
from app.domain.schemas import Evidence
from app.services.wiki.retrieve import blend_wiki_evidence, search_wiki
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_MODE", "balanced")
    # Hermetic keyword tests: the embed branch reads the default DB, which the
    # wiki_env fixture does not control. It gets its own filter test below.
    monkeypatch.setenv("FORMUMIND_WIKI_EMBED_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_iso.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return wiki


def _md(title: str, summary: str) -> str:
    return dump_page(
        kind="material",
        title=title,
        entity_id=f"material:{title}",
        norm_key=title.lower(),
        source_ids=["s1"],
        summary=summary,
    )


def _seed(wiki: WikiStore) -> None:
    wiki.upsert_page(
        path="materials/epoxy-a.md",
        kind="material",
        title="Epoxy A",
        norm_key="epoxy a",
        entity_id="material:epoxy-a",
        markdown=_md("Epoxy A", "环氧树脂 A 用于项目甲的防腐底漆。"),
        source_ids=["s1"],
        project_id="proj-A",
    )
    wiki.upsert_page(
        path="materials/epoxy-b.md",
        kind="material",
        title="Epoxy B",
        norm_key="epoxy b",
        entity_id="material:epoxy-b",
        markdown=_md("Epoxy B", "环氧树脂 B 用于项目乙的面漆。"),
        source_ids=["s2"],
        project_id="proj-B",
    )
    wiki.upsert_page(
        path="materials/legacy.md",
        kind="material",
        title="Legacy",
        norm_key="legacy",
        entity_id="material:legacy",
        markdown=_md("Legacy", "历史遗留页面。"),
        source_ids=["s3"],
    )


def test_upsert_stamps_project_id(wiki_env):
    _seed(wiki_env)
    row = wiki_env.get_by_path("materials/epoxy-a.md")
    assert row is not None
    assert row.project_id == "proj-A"
    legacy = wiki_env.get_by_path("materials/legacy.md")
    assert legacy is not None
    assert legacy.project_id is None


def test_update_adopts_project_id_when_null(wiki_env):
    _seed(wiki_env)
    wiki_env.upsert_page(
        path="materials/legacy.md",
        kind="material",
        title="Legacy v2",
        norm_key="legacy",
        entity_id="material:legacy",
        markdown=_md("Legacy", "历史遗留页面更新。"),
        source_ids=["s3"],
        project_id="proj-A",
    )
    row = wiki_env.get_by_path("materials/legacy.md")
    assert row is not None
    assert row.project_id == "proj-A"


def test_cross_project_path_collision_refuses(wiki_env):
    """Cross-project collision now raises (fail-closed), not just warns."""
    _seed(wiki_env)
    with pytest.raises(WikiProjectCollisionError, match="belongs to project"):
        wiki_env.upsert_page(
            path="materials/epoxy-a.md",
            kind="material",
            title="Epoxy A hijack",
            norm_key="epoxy a",
            entity_id="material:epoxy-a",
            markdown=_md("Epoxy A", "changed body triggers update branch"),
            source_ids=["s9"],
            project_id="proj-B",
        )
    # Existing scope and content are preserved (not overwritten).
    row = wiki_env.get_by_path("materials/epoxy-a.md")
    assert row is not None
    assert row.project_id == "proj-A"
    assert "项目甲" in (wiki_env.read_markdown("materials/epoxy-a.md") or "")


def test_list_pages_project_filter(wiki_env):
    _seed(wiki_env)
    paths_a = {r.path for r in wiki_env.list_pages(project_id="proj-A")}
    assert paths_a == {"materials/epoxy-a.md"}
    paths_b = {r.path for r in wiki_env.list_pages(project_id="proj-B")}
    assert paths_b == {"materials/epoxy-b.md"}
    # Unfiltered keeps back-compat.
    assert len(wiki_env.list_pages()) == 3


def test_search_wiki_project_filter(wiki_env):
    _seed(wiki_env)
    hits_a = search_wiki("环氧树脂", k=5, project_id="proj-A")
    assert {h.identifier for h in hits_a} == {"wiki:materials/epoxy-a.md"}
    hits_b = search_wiki("环氧树脂", k=5, project_id="proj-B")
    assert {h.identifier for h in hits_b} == {"wiki:materials/epoxy-b.md"}
    # Legacy NULL page is excluded from project-scoped retrieval.
    hits_legacy = search_wiki("历史遗留", k=5, project_id="proj-A")
    assert hits_legacy == []


def test_blend_wiki_evidence_project_filter(wiki_env):
    _seed(wiki_env)
    sources: list[Evidence] = []
    merged, added = blend_wiki_evidence("环氧树脂", sources, project_id="proj-A")
    assert added == 1
    assert merged[0].identifier == "wiki:materials/epoxy-a.md"
    merged_b, added_b = blend_wiki_evidence("环氧树脂", sources, project_id="proj-B")
    assert added_b == 1
    assert merged_b[0].identifier == "wiki:materials/epoxy-b.md"


def test_search_wiki_embedded_project_filter(tmp_path, monkeypatch):
    """The semantic branch (default ON) must also respect project scope."""
    import app.db.chunk_store as chunk_store_mod
    from app.db.database import make_engine as _make_engine
    from app.db.database import make_session_factory as _make_session_factory
    from app.db.chunk_store import ChunkStore
    from app.db.models import Base as _Base
    from app.db.models import SourceDocument
    from app.db.session_utils import commit_session
    from app.services.wiki.embed import (
        search_wiki_embedded,
        wiki_origin_url,
        wiki_source_id,
    )

    monkeypatch.setenv("FORMUMIND_WIKI_EMBED_ENABLED", "true")
    get_settings.cache_clear()

    engine = _make_engine(f"sqlite:///{tmp_path}/embed_iso.db")
    _Base.metadata.create_all(engine)
    factory = _make_session_factory(engine)
    monkeypatch.setattr("app.db.database.default_session_factory", lambda: factory)
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))

    def _seed_wiki_doc(rel: str, project_id: str | None, text: str) -> None:
        sid = wiki_source_id(rel)
        with commit_session(factory) as session:
            session.add(
                SourceDocument(
                    id=sid,
                    filename=rel,
                    title=rel,
                    source_kind="wiki",
                    content_hash="x",
                    origin_url=wiki_origin_url(rel),
                    full_text=text,
                    raw_text_chars=len(text),
                    extraction_status="skipped",
                    project_id=project_id,
                )
            )
            chunk_store_mod.get_chunk_store().replace_for_source_in(
                session,
                sid,
                [
                    {
                        "text": text,
                        "heading_path": "wiki/material",
                        "meta": {"wiki": True, "wiki_path": rel, "wiki_kind": "material"},
                    }
                ],
            )

    _seed_wiki_doc("materials/epoxy-a.md", "proj-A", "环氧树脂 A 用于项目甲的防腐底漆。")
    _seed_wiki_doc("materials/epoxy-b.md", "proj-B", "环氧树脂 B 用于项目乙的面漆。")
    _seed_wiki_doc("materials/legacy.md", None, "环氧树脂历史遗留页面。")

    hits_a = search_wiki_embedded("环氧树脂", k=5, project_id="proj-A")
    assert {h.identifier for h in hits_a} == {"wiki:materials/epoxy-a.md"}
    hits_b = search_wiki_embedded("环氧树脂", k=5, project_id="proj-B")
    assert {h.identifier for h in hits_b} == {"wiki:materials/epoxy-b.md"}
    # Unscoped keeps the old global behavior.
    hits_all = search_wiki_embedded("环氧树脂", k=5)
    assert {h.identifier for h in hits_all} == {
        "wiki:materials/epoxy-a.md",
        "wiki:materials/epoxy-b.md",
        "wiki:materials/legacy.md",
    }
