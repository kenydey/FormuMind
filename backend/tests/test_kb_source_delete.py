"""Knowledge Hub H2: cascade delete KB sources."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.kb_delete import delete_kb_source
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_mod
    import app.db.source_store as source_mod
    import app.db.wiki_store as wiki_mod

    db = tmp_path / "del.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(source_mod, "_store", src)
    monkeypatch.setattr(chunk_mod, "_store", chk)
    monkeypatch.setattr(wiki_mod, "_store", wiki)
    get_settings.cache_clear()
    return src, chk, wiki


def test_delete_kb_source_cascades_chunks(env):
    src, chk, wiki = env
    sid = src.create(
        filename="a.pdf",
        title="Doc A",
        source_kind="patent",
        full_text="x " * 80,
        content_hash="h-del-1",
        origin_url="CN123456A",
    )
    chk.replace_for_source(sid, [{"text": "chunk one " * 20}, {"text": "chunk two " * 20}])
    assert len(chk.get_by_source(sid)) == 2
    wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown=dump_page(
            kind="material",
            title="E-51",
            entity_id="material:e51",
            norm_key="e51",
            source_ids=[sid],
            summary="linked",
        ),
        source_ids=[sid],
    )
    out = delete_kb_source(sid)
    assert out["ok"] is True
    assert out["chunks_removed"] == 2
    assert src.get(sid) is None
    assert chk.get_by_source(sid) == []
    page = wiki.get_by_path("materials/e51.md")
    assert page is not None
    assert sid not in (page.source_ids or [])
    assert "stale" in (page.flags or [])


def test_delete_missing_source_404(env):
    client = TestClient(app)
    r = client.delete("/api/kb/sources/does-not-exist")
    assert r.status_code == 404


def test_delete_via_api(env):
    src, chk, _ = env
    sid = src.create(
        filename="b.pdf",
        title="Doc B",
        source_kind="upload",
        full_text="y " * 80,
        content_hash="h-del-2",
    )
    chk.replace_for_source(sid, [{"text": "only chunk " * 30}])
    client = TestClient(app)
    r = client.delete(f"/api/kb/sources/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["chunks_removed"] == 1
    assert src.get(sid) is None
