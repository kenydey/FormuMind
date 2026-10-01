"""KB ingest-time near-duplicate dedup (2026-10-01).

Covers L1 exact (normalized-text sha256) and L2 near (embedding cosine),
the fail-open behaviour, gate counters, and the re-ingest self-exclusion.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import DocumentChunk
from app.services import kb_dedup
from app.services.kb_dedup import chunk_dedup_key, dedupe_chunk_rows, normalize_text
from app.services.kb_retrieval_gate import (
    gate_drop_stats,
    reset_gate_drop_stats,
)


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture(autouse=True)
def _clean_gate():
    reset_gate_drop_stats()
    yield
    reset_gate_drop_stats()
    # Isolation: make_engine() (factory fixture) populates the get_settings
    # lru_cache during fixture setup — before test-body monkeypatch.setenv.
    from app.config import get_settings

    get_settings.cache_clear()


def _refresh_settings(monkeypatch, **env):
    """Set env flags then drop the get_settings lru_cache.

    Must be called in the test body (after all fixtures are set up):
    the ``factory`` fixture's ``make_engine()`` already called get_settings()
    once during fixture setup, so a bare monkeypatch.setenv would be
    invisible to the cached instance.
    """
    from app.config import get_settings

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def _row(text, **kw):
    d = {"text": text, "heading_path": "", "lang": "en"}
    d.update(kw)
    return d


def _seed(factory, source_id, text, lang="en", vec=None):
    blob = None
    if vec is not None:
        blob = np.asarray(vec, dtype="<f4").tobytes()
    with factory() as s:
        s.add(
            DocumentChunk(
                id=str(uuid.uuid4()),
                source_id=source_id,
                text=text,
                lang=lang,
                embedding_blob=blob,
            )
        )
        s.commit()


def _count(factory, source_id):
    with factory() as s:
        return (
            s.query(DocumentChunk).filter(DocumentChunk.source_id == source_id).count()
        )


def test_normalize_text_collapses_whitespace_and_case():
    assert normalize_text("  Hello   World\n") == "hello world"
    assert chunk_dedup_key("  Hello   World\n", "", None) == chunk_dedup_key("hello world", "", None)
    # Provenance is part of the key.
    assert chunk_dedup_key("hello world", "sec 1", None) != chunk_dedup_key("hello world", "sec 2", None)


def test_l1_exact_cross_source_skip(factory):
    """Chunk text already present under another source → dropped."""
    _seed(factory, "src-a", "Epoxy coating formulation with zinc phosphate.")
    with factory() as s:
        kept = dedupe_chunk_rows(
            [_row("  epoxy COATING formulation with zinc phosphate. "),
             _row("Something completely different here.")],
            "src-b",
            s,
        )
    assert len(kept) == 1
    assert "different" in kept[0]["text"]
    assert gate_drop_stats()["ingest"]["dedup_exact"] == 1


def test_l1_same_text_different_provenance_kept(factory):
    """Same-document chunks may share text across sections — different
    heading_path/page_no means different provenance, NOT duplicates."""
    _seed(factory, "src-a", "Shared body text.")
    with factory() as s:
        kept = dedupe_chunk_rows(
            [
                _row("Shared body text.", heading_path="sec 1"),
                _row("Shared body text.", heading_path="sec 2"),
                _row("Shared body text.", heading_path="sec 1", page_no=2),
            ],
            "src-b",
            s,
        )
    # None matches src-a's ("", None) provenance → all kept.
    assert len(kept) == 3
    assert gate_drop_stats()["ingest"]["dedup_exact"] == 0


def test_l1_cross_source_provenance_mismatch_kept(factory):
    """Same text under another source but different heading → kept."""
    _seed(factory, "src-a", "Shared body text.")
    with factory() as s:
        # seed a chunk with provenance under src-a
        from app.db.models import DocumentChunk
        import uuid as _uuid

        s.add(
            DocumentChunk(
                id=str(_uuid.uuid4()),
                source_id="src-a",
                ord=1,
                text="Other text.",
                heading_path="sec 9",
                lang="en",
            )
        )
        s.commit()
        kept = dedupe_chunk_rows(
            [_row("Other text.", heading_path="sec 1")],
            "src-b",
            s,
        )
    assert len(kept) == 1


def test_l1_reingest_same_source_not_dropped(factory):
    """Re-ingesting the same source must not nuke its own chunks.

    replace_for_source deletes the source's old rows AFTER dedup runs, so
    the L1/L2 queries must exclude the current source_id.
    """
    _seed(factory, "src-a", "Stable document text.")
    with factory() as s:
        kept = dedupe_chunk_rows([_row("Stable document text.")], "src-a", s)
    assert len(kept) == 1
    assert gate_drop_stats()["ingest"]["dedup_exact"] == 0


def test_l1_disabled_keeps_everything(factory, monkeypatch):
    _refresh_settings(monkeypatch, FORMUMIND_KB_DEDUP_EXACT_ENABLED="false")
    _seed(factory, "src-a", "Duplicated text.")
    with factory() as s:
        kept = dedupe_chunk_rows([_row("Duplicated text.")], "src-b", s)
    assert len(kept) == 1


def test_l2_near_enforce_drops_identical_vector(factory, monkeypatch):
    _refresh_settings(monkeypatch, FORMUMIND_KB_NEAR_DEDUP_ENABLED="true")
    v = [1.0, 0.0, 0.0, 0.0]
    _seed(factory, "src-a", "Some anticorrosion coating text.", vec=v)
    with factory() as s:
        kept = dedupe_chunk_rows(
            [
                _row("Totally different wording here.", embedding=list(v)),
                _row("Orthogonal content vector.", embedding=[0.0, 1.0, 0.0, 0.0]),
                _row("No vector row at all."),
            ],
            "src-b",
            s,
        )
    assert [r["text"] for r in kept] == [
        "Orthogonal content vector.",
        "No vector row at all.",
    ]
    assert gate_drop_stats()["ingest"]["dedup_near"] == 1


def test_l2_threshold_boundary(factory, monkeypatch):
    """0.98 threshold: 0.979 kept, 0.999 dropped (unit vectors)."""
    _refresh_settings(monkeypatch, FORMUMIND_KB_NEAR_DEDUP_ENABLED="true")
    _seed(factory, "src-a", "Seed text.", vec=[1.0, 0.0])
    import math

    def vec_at(cos):
        return [cos, math.sqrt(max(0.0, 1 - cos * cos))]

    with factory() as s:
        kept = dedupe_chunk_rows(
            [_row("below", embedding=vec_at(0.979)), _row("above", embedding=vec_at(0.999))],
            "src-b",
            s,
        )
    assert [r["text"] for r in kept] == ["below"]


def test_l2_audit_only_keeps_row_but_logs(factory, monkeypatch, caplog):
    """Default: near-dedup audit on, enforce off → row kept, audit logged."""
    # defaults: kb_near_dedup_enabled=False, kb_near_dedup_audit=True
    v = [1.0, 0.0, 0.0, 0.0]
    _seed(factory, "src-a", "Seed text.", vec=v)
    with factory() as s, caplog.at_level("INFO", logger="app.services.kb_dedup"):
        kept = dedupe_chunk_rows([_row("dup", embedding=list(v))], "src-b", s)
    assert len(kept) == 1
    assert gate_drop_stats()["ingest"]["dedup_near"] == 0
    assert any("audit" in rec.message for rec in caplog.records)


def test_l2_cross_lang_not_compared(factory, monkeypatch):
    """Vectors live in per-language populations; zh seed must not nuke en row."""
    _refresh_settings(monkeypatch, FORMUMIND_KB_NEAR_DEDUP_ENABLED="true")
    v = [1.0, 0.0, 0.0, 0.0]
    _seed(factory, "src-a", "中文种子文本", lang="zh", vec=v)
    with factory() as s:
        kept = dedupe_chunk_rows(
            [_row("en row", lang="en", embedding=list(v))], "src-b", s
        )
    assert len(kept) == 1


def test_l2_dim_mismatch_fail_open(factory, monkeypatch):
    """Row dim != DB dim (different model bucket) → kept, no crash."""
    _refresh_settings(monkeypatch, FORMUMIND_KB_NEAR_DEDUP_ENABLED="true")
    _seed(factory, "src-a", "Seed.", vec=[1.0, 0.0, 0.0, 0.0])
    with factory() as s:
        kept = dedupe_chunk_rows(
            [_row("row", embedding=[1.0, 0.0])], "src-b", s
        )
    assert len(kept) == 1


def test_fail_open_on_broken_session():
    """Any DB failure → rows returned unchanged, ingest never blocked."""

    class Boom:
        @property
        def query(self):
            raise RuntimeError("db gone")

    rows = [_row("a"), _row("b")]
    assert dedupe_chunk_rows(rows, "src-x", Boom()) == rows


def test_dedupe_empty_rows_no_db_touch(factory):
    with factory() as s:
        assert dedupe_chunk_rows([], "src-x", s) == []


def test_end_to_end_ingest_dedup_then_write(factory):
    """Dedup output feeds replace_for_source_in unchanged in shape."""
    _seed(factory, "src-a", "Already indexed text.")
    store = ChunkStore(factory)
    with factory() as s:
        rows = dedupe_chunk_rows(
            [_row("Already indexed text."), _row("Fresh text here.")], "src-b", s
        )
        n = store.replace_for_source_in(s, "src-b", rows)
        s.commit()
    assert n == 1
    assert _count(factory, "src-b") == 1
    assert _count(factory, "src-a") == 1
