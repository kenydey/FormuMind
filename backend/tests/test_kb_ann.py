"""Phase C-1: faiss ANN index over KB chunk embeddings.

Verifies:
1. vector_to_blob / chunk_vector roundtrip (BLOB) + JSON fallback;
2. faiss build + search correctness: recall@k == 1.0 vs brute-force cosine
   on synthetic vectors (IndexFlatIP over normalised vectors is exact);
3. per-model buckets never mix semantic spaces;
4. index persistence across invalidate() (disk load);
5. DB-fingerprint staleness: new ingest / replace (same count) / delete /
   persisted-reload-after-change all trigger a rebuild — never stale vectors;
6. fail-open: disabled setting / empty corpus / corrupt files;
7. chunk_store dual-write + embedded_vectors + embedded_fingerprint +
   chunks_by_ids;
8. 0039 migration upgrade/downgrade/backfill idempotency.
"""
from __future__ import annotations

import json
import math
import os
import struct

import pytest

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from app.db import chunk_store as cs_mod
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_ann


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    # Redirect the faiss index dir to a temp dir; reset all process-local state.
    def _tmpdir():
        d = str(tmp_path / "kb_ann")
        os.makedirs(d, exist_ok=True)
        return d

    monkeypatch.setattr(kb_ann, "_index_dir", _tmpdir)
    kb_ann.invalidate()
    monkeypatch.setattr(cs_mod, "_store", None)
    yield
    kb_ann.invalidate()
    monkeypatch.setattr(cs_mod, "_store", None)


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    import app.db.database as db_mod

    engine = make_engine(f"sqlite:///{tmp_path}/ann.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    yield factory


def _normed(dim: int, seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return [float(x) for x in v]


def _seed_chunks(n: int, dim: int = 64, model: str = "test-model") -> list[dict]:
    chunks = []
    for i in range(n):
        chunks.append(
            {
                "text": f"chunk text {i} about chemical formulation",
                "embedding": _normed(dim, 1000 + i),
                "embedding_model": model,
            }
        )
    return chunks


# ── vector codec ──────────────────────────────────────────────────────────


def test_vector_blob_roundtrip():
    vec = _normed(32, 7)
    blob = kb_ann.vector_to_blob(vec)
    assert isinstance(blob, bytes) and len(blob) == 32 * 4
    back = np.frombuffer(blob, dtype="<f4")
    assert np.allclose(back, np.asarray(vec, dtype=np.float32), atol=1e-6)


def test_chunk_vector_prefers_blob(tmpdb):
    from types import SimpleNamespace

    vec = _normed(16, 11)
    chunk = SimpleNamespace(
        embedding_blob=kb_ann.vector_to_blob(vec),
        embedding=[0.0] * 16,  # stale JSON must be ignored when blob exists
    )
    out = kb_ann.chunk_vector(chunk)
    assert out is not None
    assert np.allclose(out, np.asarray(vec, dtype=np.float32), atol=1e-6)


def test_chunk_vector_json_fallback():
    from types import SimpleNamespace

    vec = _normed(16, 13)
    chunk = SimpleNamespace(embedding_blob=None, embedding=vec)
    out = kb_ann.chunk_vector(chunk)
    assert out is not None
    assert np.allclose(out, np.asarray(vec, dtype=np.float32), atol=1e-6)
    # normalised
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5


def test_chunk_vector_none():
    from types import SimpleNamespace

    assert kb_ann.chunk_vector(SimpleNamespace(embedding_blob=None, embedding=None)) is None
    assert kb_ann.chunk_vector(SimpleNamespace(embedding_blob=None, embedding=[])) is None


# ── index build / search correctness ───────────────────────────────────────


def _brute_topk(vecs: list[list[float]], q: list[float], k: int) -> list[int]:
    qv = np.asarray(q, dtype=np.float32)
    scores = [float(np.dot(np.asarray(v, dtype=np.float32), qv)) for v in vecs]
    order = sorted(range(len(vecs)), key=lambda i: scores[i], reverse=True)
    return order[:k]


def test_build_and_search_recall_at_k(tmpdb):
    store = cs_mod.get_chunk_store()
    n, dim = 200, 64
    chunks = _seed_chunks(n, dim)
    store.replace_for_source("src-1", chunks)

    # vectors for brute-force reference (JSON column, as written)
    vecs = [c["embedding"] for c in chunks]

    res = kb_ann.ensure_index()
    assert res["ready"] is True
    assert res["buckets"]["buckets"]["test-model"]["count"] == n

    for seed in (4242, 777, 12345):
        q = _normed(dim, seed)
        k = 10
        hits = kb_ann.search("test-model", q, k)
        assert hits is not None and len(hits) == k
        got = [cid for cid, _ in hits]
        # map chunk ids back to positions via DB order is unstable; instead
        # compare score multisets against brute force top-k scores.
        qv = np.asarray(q, dtype=np.float32)
        brute_scores = sorted(
            (float(np.dot(np.asarray(v, dtype=np.float32), qv)) for v in vecs),
            reverse=True,
        )[:k]
        got_scores = sorted((s for _, s in hits), reverse=True)
        for a, b in zip(got_scores, brute_scores):
            assert abs(a - b) < 1e-5, f"faiss {a} != brute {b}"
        # recall@k == 1.0 on ids: brute-force top-k ids vs faiss top-k ids.
        brute_idx = set(_brute_topk(vecs, q, k))
        # resolve faiss chunk ids to their seed positions via text lookup
        rows = store.chunks_by_ids(got)
        got_idx = set()
        for r in rows:
            # text is "chunk text {i} about chemical formulation"
            got_idx.add(int(r.text.split()[2]))
        assert got_idx == brute_idx


def test_model_buckets_dont_mix(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(20, 32, model="model-a"))
    store.replace_for_source("src-2", _seed_chunks(20, 48, model="model-b"))
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    buckets = res["buckets"]["buckets"]
    assert buckets["model-a"]["dim"] == 32
    assert buckets["model-b"]["dim"] == 48
    # wrong-dim query against a bucket -> None (no silent truncation)
    assert kb_ann.search("model-a", _normed(48, 1), 5) is None
    hits = kb_ann.search("model-b", _normed(48, 2), 5)
    assert hits is not None and len(hits) == 5


def test_persist_and_reload(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(30, 32))
    assert kb_ann.ensure_index()["ready"] is True
    stats_before = kb_ann.index_stats()
    assert stats_before["loaded"] is True

    kb_ann.invalidate()
    assert kb_ann.index_stats()["loaded"] is False

    res = kb_ann.ensure_index()
    assert res["ready"] is True
    assert res["buckets"]["buckets"]["test-model"]["count"] == 30
    hits = kb_ann.search("test-model", _normed(32, 99), 5)
    assert hits is not None and len(hits) == 5


# ── DB-fingerprint staleness (C-1d) ─────────────────────────────────────────
# Any chunk insert/delete/replace/archive must invalidate the faiss index:
# the next ensure_index() rebuilds from the DB instead of serving stale
# vectors. These are real write-path scenarios, not synthetic re-adds.


def test_new_ingest_triggers_rebuild(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    assert kb_ann.ensure_index()["ready"] is True

    store.replace_for_source("src-2", _seed_chunks(5, 32))
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    assert res["buckets"]["buckets"]["test-model"]["count"] == 15
    hits = kb_ann.search("test-model", _normed(32, 99), 15)
    assert hits is not None and len(hits) == 15


def test_replace_same_count_rebuilds_and_evicts_old_ids(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    assert kb_ann.ensure_index()["ready"] is True
    old_ids = {cid for cid, _ in kb_ann.search("test-model", _normed(32, 99), 10)}
    assert len(old_ids) == 10

    # Replace: same chunk count, all-new rows (new UUIDs, new created_at).
    # Count alone cannot catch this — max_created_at in the fingerprint can.
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    assert res["buckets"]["buckets"]["test-model"]["count"] == 10
    new_ids = {cid for cid, _ in kb_ann.search("test-model", _normed(32, 99), 10)}
    assert len(new_ids) == 10
    assert old_ids.isdisjoint(new_ids), "stale chunk ids must not survive replace"


def test_delete_for_source_triggers_rebuild(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    store.replace_for_source("src-2", _seed_chunks(5, 32))
    assert kb_ann.ensure_index()["ready"] is True

    # v16 P1-7: delete_for_source 返回 {"total", "embedded"} 明细
    assert store.delete_for_source("src-2")["total"] == 5
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    assert res["buckets"]["buckets"]["test-model"]["count"] == 10


def test_persisted_index_rebuilds_after_db_change(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    assert kb_ann.ensure_index()["ready"] is True

    kb_ann.invalidate()  # drop in-memory copy; disk copy remains
    store.replace_for_source("src-2", _seed_chunks(5, 32))  # DB moves on
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    # Fingerprint mismatch on the persisted manifest -> rebuild, not stale load.
    assert res["buckets"]["buckets"]["test-model"]["count"] == 15


def test_persisted_index_loads_when_db_unchanged(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    first = kb_ann.ensure_index()
    assert first["ready"] is True
    built_at = first["buckets"]["built_at"]

    kb_ann.invalidate()
    res = kb_ann.ensure_index()
    assert res["ready"] is True
    # Same fingerprint -> served from disk, no rebuild.
    assert res["buckets"]["built_at"] == built_at
    assert res["buckets"]["buckets"]["test-model"]["count"] == 10


def test_embedded_fingerprint_tracks_writes(tmpdb):
    store = cs_mod.get_chunk_store()
    assert store.embedded_fingerprint() == {}
    store.replace_for_source("src-1", _seed_chunks(4, 32))
    fp1 = store.embedded_fingerprint()
    assert fp1["test-model"]["count"] == 4
    store.replace_for_source("src-2", _seed_chunks(2, 32))
    fp2 = store.embedded_fingerprint()
    assert fp2["test-model"]["count"] == 6
    assert fp1 != fp2
    store.delete_for_source("src-2")
    fp3 = store.embedded_fingerprint()
    assert fp3["test-model"]["count"] == 4
    assert fp3 != fp2


def test_ensure_index_empty_corpus_not_ready(tmpdb):
    res = kb_ann.ensure_index()
    assert res["ready"] is False
    assert res["error"]


def test_search_unknown_model_returns_none(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(10, 32))
    assert kb_ann.ensure_index()["ready"] is True
    assert kb_ann.search("nope", _normed(32, 5), 5) is None


# ── fail-open in hybrid_search ─────────────────────────────────────────────


def test_faiss_vector_scores_disabled_setting():
    from types import SimpleNamespace

    from app.services import hybrid_search as hs

    settings = SimpleNamespace(kb_ann_enabled=False)
    assert hs._faiss_vector_scores("query", settings, top_k=10, project_id=None, include_global=False) is None


def test_faiss_vector_scores_no_index_never_raises(tmpdb):
    from types import SimpleNamespace

    from app.services import hybrid_search as hs

    settings = SimpleNamespace(kb_ann_enabled=True, kb_hybrid_ann_candidate_pool=800)
    # empty corpus -> index not ready -> None, no exception
    out = hs._faiss_vector_scores("query", settings, top_k=10, project_id=None, include_global=False)
    assert out is None


# ── chunk_store ────────────────────────────────────────────────────────────


def test_add_chunks_dual_write(tmpdb):
    store = cs_mod.get_chunk_store()
    vec = _normed(16, 21)
    store.replace_for_source("src-1", [{"text": "t", "embedding": vec, "embedding_model": "m"}])
    from app.db.models import DocumentChunk

    import app.db.database as db_mod

    factory = db_mod.default_session_factory()
    with factory() as session:
        row = session.query(DocumentChunk).one()
        assert row.embedding is not None  # legacy JSON kept
        assert row.embedding_blob is not None
        back = np.frombuffer(bytes(row.embedding_blob), dtype="<f4")
        assert np.allclose(back, np.asarray(vec, dtype=np.float32), atol=1e-6)


def test_embedded_vectors_scan(tmpdb):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed_chunks(5, 16))
    store.replace_for_source("src-2", [{"text": "no vector"}])
    rows = store.embedded_vectors()
    assert len(rows) == 5
    assert all(r["embedding_model"] == "test-model" for r in rows)
    assert all(r["embedding_blob"] for r in rows)


def test_chunks_by_ids_project_filter(tmpdb):
    import app.db.database as db_mod
    from app.db.models import SourceDocument

    store = cs_mod.get_chunk_store()
    factory = db_mod.default_session_factory()
    with factory() as session:
        session.add(SourceDocument(id="s-a", project_id="p-a", content_hash="h-a"))
        session.add(SourceDocument(id="s-b", project_id="p-b", content_hash="h-b"))
        session.commit()
    store.replace_for_source("s-a", [{"text": "a"}])
    store.replace_for_source("s-b", [{"text": "b"}])
    with factory() as session:
        from app.db.models import DocumentChunk

        ids = [r.id for r in session.query(DocumentChunk.id).all()]
    got_a = store.chunks_by_ids(ids, "p-a")
    assert {c.text for c in got_a} == {"a"}
    got_all = store.chunks_by_ids(ids)
    assert len(got_all) == 2


# ── migration 0039 ─────────────────────────────────────────────────────────


def test_migration_0039_upgrade_downgrade(tmp_path, monkeypatch):
    from tests.alembic_helpers import run_downgrade, run_upgrade
    from tests.test_alembic_migrations import _column_names

    db_url = f"sqlite:///{tmp_path}/m39.db"
    # Note: the 0001 baseline does create_all from the current models, so the
    # column already exists even at 0038 (same pattern as 0037/0038) — the
    # 0039 upgrade guard skips the DDL idempotently.
    run_upgrade(db_url, monkeypatch, "0038")
    run_upgrade(db_url, monkeypatch, "head")
    assert "embedding_blob" in _column_names(db_url, "document_chunks")

    run_downgrade(db_url, monkeypatch, "0038")
    assert "embedding_blob" not in _column_names(db_url, "document_chunks")

    # re-upgrade idempotent
    run_upgrade(db_url, monkeypatch, "head")
    run_upgrade(db_url, monkeypatch, "head")
    assert "embedding_blob" in _column_names(db_url, "document_chunks")


def test_migration_0039_backfills_json(tmp_path, monkeypatch):
    import sqlalchemy as sa
    from tests.alembic_helpers import run_upgrade

    db_url = f"sqlite:///{tmp_path}/m39b.db"
    run_upgrade(db_url, monkeypatch, "0038")
    eng = sa.create_engine(db_url)
    vec = [0.1, 0.2, 0.3, 0.4]
    with eng.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO document_chunks (id, source_id, ord, text, heading_path, embedding, embedding_model, created_at) "
                "VALUES ('c1', 's1', 0, 't', '', :emb, 'm', '2026-10-01 00:00:00')"
            ),
            {"emb": json.dumps(vec)},
        )
    run_upgrade(db_url, monkeypatch, "head")
    with eng.connect() as conn:
        blob = conn.execute(sa.text("SELECT embedding_blob FROM document_chunks WHERE id='c1'")).scalar()
    assert blob is not None
    back = struct.unpack("<4f", bytes(blob))
    for a, b in zip(back, vec):
        assert abs(a - b) < 1e-6
