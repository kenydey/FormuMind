"""Phase 3: Wiki summary embed into document_chunks (dual-track)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import SourceDocument
from app.db.wiki_store import WikiStore
from app.domain.schemas import Evidence
from app.main import app
from app.services.wiki.embed import (
    embed_wiki_page,
    list_wiki_source_ids,
    search_wiki_embedded,
    wiki_source_id,
)
from app.services.wiki.retrieve import blend_wiki_evidence, filter_raw_evidence, search_wiki
from app.services.wiki.schema import dump_page, system_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_EMBED_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as database_mod
    import app.db.source_store as source_store_mod
    import app.db.wiki_store as wiki_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore

    db_path = tmp_path / "wiki_p3.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)

    chunks = ChunkStore(factory)
    sources = SourceStore(factory)
    monkeypatch.setattr(chunk_store_mod, "_store", chunks)
    monkeypatch.setattr(source_store_mod, "_store", sources)
    monkeypatch.setattr(database_mod, "default_session_factory", lambda: factory)
    get_settings.cache_clear()
    return wiki, factory, chunks


def _seed_system(wiki: WikiStore, key: str = "epoxy-cure") -> str:
    path = system_path(key)
    wiki.upsert_page(
        path=path,
        kind="system",
        title="环氧固化体系",
        norm_key=key,
        entity_id=f"system:{key}",
        markdown=dump_page(
            kind="system",
            title="环氧固化体系",
            entity_id=f"system:{key}",
            norm_key=key,
            source_ids=["s1"],
            summary="胺固化环氧体系，注意催化剂窗口与暴聚风险。",
            evidence_blocks=["### Source `s1`", "催化剂建议 ≤1.5 wt%。"],
            bounds=[{"name": "catalyst_wt", "min": 0.1, "max": 1.5, "unit": "wt%"}],
        ),
        source_ids=["s1"],
        replace_source_ids=True,
    )
    return path


def test_embed_flag_off_skips(wiki_env, monkeypatch):
    wiki, _, _ = wiki_env
    path = _seed_system(wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_EMBED_ENABLED", "false")
    get_settings.cache_clear()
    out = embed_wiki_page(path)
    assert out.get("skipped") is True


def test_embed_writes_source_and_chunk(wiki_env):
    wiki, _, chunks = wiki_env
    path = _seed_system(wiki)
    out = embed_wiki_page(path)
    assert out.get("ok") is True
    sid = wiki_source_id(path)
    assert sid in list_wiki_source_ids()
    rows = chunks.get_by_source(sid)
    assert len(rows) == 1
    assert "暴聚" in (rows[0].text or "")
    assert (rows[0].meta or {}).get("wiki_path") == path


def test_search_wiki_embedded_finds_summary(wiki_env):
    wiki, _, _ = wiki_env
    path = _seed_system(wiki)
    embed_wiki_page(path)
    hits = search_wiki_embedded("暴聚风险", k=5)
    assert hits
    assert all(h.source == "wiki" for h in hits)
    assert any(path in (h.identifier or "") for h in hits)


def test_search_wiki_merges_embedded(wiki_env):
    wiki, _, _ = wiki_env
    path = _seed_system(wiki)
    embed_wiki_page(path)
    hits = search_wiki("催化剂窗口", k=5)
    assert hits
    assert any(h.identifier == f"wiki:{path}" for h in hits)


def test_claims_still_raw_only_after_embed(wiki_env):
    wiki, _, _ = wiki_env
    path = _seed_system(wiki)
    embed_wiki_page(path)
    wiki_hits = search_wiki("环氧", k=3)
    raw = [
        Evidence(
            source="literature",
            identifier="doi:1",
            title="raw paper",
            snippet="raw epoxy cure",
            relevance=0.7,
        )
    ]
    merged, n = blend_wiki_evidence("环氧固化", raw)
    assert n >= 1
    assert any(h.source == "wiki" for h in merged)
    claims = filter_raw_evidence(merged + wiki_hits)
    assert all(c.source != "wiki" for c in claims)
    assert any(c.identifier == "doi:1" for c in claims)


def test_search_chunks_excludes_wiki_sources(wiki_env):
    """Track B must not return wiki summary chunks."""
    wiki, factory, chunks = wiki_env
    path = _seed_system(wiki)
    embed_wiki_page(path)
    sid = wiki_source_id(path)

    from app.db.session_utils import commit_session

    with commit_session(factory) as session:
        session.add(
            SourceDocument(
                id="raw-src-1",
                filename="paper.pdf",
                title="Raw epoxy paper",
                source_kind="local",
                content_hash="abc",
                full_text="环氧固化催化剂窗口",
                raw_text_chars=20,
                extraction_status="skipped",
            )
        )
        chunks.replace_for_source_in(
            session,
            "raw-src-1",
            [{"text": "环氧固化催化剂窗口 来自专利原文", "heading_path": "intro"}],
        )

    from app.services.kb_index import search_chunks

    hits = search_chunks("环氧固化催化剂", k=10)
    assert all(h.source != "wiki" for h in hits)
    assert all(not (h.identifier or "").startswith(f"kb:{sid}") for h in hits)


def test_embed_rebuild_api(wiki_env):
    wiki, _, _ = wiki_env
    _seed_system(wiki)
    with TestClient(app) as c:
        r = c.post("/api/wiki/embed/rebuild")
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("indexed", 0) >= 1


def test_embed_rebuild_api_409_when_off(wiki_env, monkeypatch):
    wiki, _, _ = wiki_env
    _seed_system(wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_EMBED_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as c:
        r = c.post("/api/wiki/embed/rebuild")
        assert r.status_code == 409
