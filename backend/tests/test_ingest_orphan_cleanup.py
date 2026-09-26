"""Hard index failures must not leave orphan SourceDocument rows."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.services import ingestion


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_SOURCE_GUIDE_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def test_hard_index_failure_deletes_orphan_source(stores, monkeypatch):
    src, _chk = stores
    created: list[str] = []
    real_create = src.create

    def tracking_create(*a, **k):
        sid = real_create(*a, **k)
        created.append(sid)
        return sid

    monkeypatch.setattr(src, "create", tracking_create)

    def boom(*_a, **_k):
        raise RuntimeError("simulated index hard failure")

    monkeypatch.setattr("app.services.kb_index.index_source", boom)

    text = "Epoxy coating corrosion resistance.\n\n" * 20
    outcome = ingestion._ingest_parsed_text(
        text, filename="orphan.txt", source_kind="local", persist=True
    )
    assert outcome.source_id is None
    assert outcome.extraction_status == "failed"
    assert created, "create must have run before index failure"
    assert src.get(created[0]) is None
