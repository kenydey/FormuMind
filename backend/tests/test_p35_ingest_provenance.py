"""P3-5: parser provenance + embedding-missing alert tests."""
from __future__ import annotations

import logging

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


def _reset_coverage():
    from app.services import kb_index

    with kb_index._KB_COVERAGE_LOCK:
        kb_index._KB_COVERAGE["kb_chunks_embedded"] = 0
        kb_index._KB_COVERAGE["kb_chunks_total"] = 0


def test_parser_provenance_persisted(stores):
    src, _chk = stores
    text = "Epoxy coating corrosion resistance salt spray 1000h.\n\n" * 20
    outcome = ingestion._ingest_parsed_text(
        text, filename="doc.pdf", source_kind="local", persist=True, parser="hybrid"
    )
    assert outcome.source_id
    row = src.get(outcome.source_id)
    assert row is not None
    assert row.parser == "hybrid"


def test_parser_defaults_to_none(stores):
    src, _chk = stores
    text = "Pasted text without a parser.\n\n" * 20
    outcome = ingestion._ingest_parsed_text(
        text, filename="note.txt", source_kind="local", persist=True
    )
    assert outcome.source_id
    assert src.get(outcome.source_id).parser is None


def test_embedding_missing_structured_alert(stores, monkeypatch, caplog):
    from app.services import kb_index

    _reset_coverage()
    # Simulate sentence-transformers unavailable.
    monkeypatch.setattr(kb_index, "_embed_texts", lambda texts, mname=None: None)

    with caplog.at_level(logging.WARNING, logger="app.services.kb_index"):
        n = kb_index.index_source(
            "src-p35", "Epoxy coating corrosion resistance.\n\n" * 30, embed=True
        )
    assert n > 0  # chunks still written (BM25 fallback)
    assert "event=kb_embedding_missing" in caplog.text

    cov = kb_index.get_kb_coverage_stats()
    assert cov["kb_chunks_total"] > 0
    assert cov["kb_chunks_embedded"] == 0


def test_evidence_stats_exposes_kb_coverage():
    from app.api.ops import evidence_stats
    from app.services import kb_index

    _reset_coverage()
    with kb_index._KB_COVERAGE_LOCK:
        kb_index._KB_COVERAGE["kb_chunks_embedded"] = 3
        kb_index._KB_COVERAGE["kb_chunks_total"] = 4
    stats = evidence_stats()
    assert stats["kb_chunks_embedded"] == 3
    assert stats["kb_chunks_total"] == 4
    assert stats["kb_embedding_coverage"] == pytest.approx(0.75)
    _reset_coverage()
