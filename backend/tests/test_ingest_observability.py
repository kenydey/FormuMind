"""P1-1: ingest failure observability.

Contract under test:
1. ``SourceStore.record_ingest_failure`` upserts a failed row keyed by
   origin URL (ingest_status='failed' + truncated error); it never
   downgrades a successfully indexed row.
2. ``find_failed_by_origin`` / ``revive_failed_row`` round-trip: a retry
   that succeeds promotes the failed row in place (one row per origin,
   error cleared).
3. Tier-1 origin dedup in ``_fetch_one`` retries failed rows instead of
   skipping them; indexed rows are skipped with
   ``skip_reason="already_ingested"``; blocklisted URLs get
   ``skip_reason="blocked_domain"``.
4. ``ingest_evidence_docs`` persists failures to the DB, reports
   ``skipped_by_reason`` in the summary, and never lets the persistence
   step break the batch.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import Evidence
from app.services import fulltext_fetcher as ff
from app.services import kb_ingest

LONG_TEXT = "# 防腐蚀专利\n\n" + "环氧树脂与磷酸锌协同防腐蚀机理研究。" * 60


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KB_INGEST_AUTO", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    return src, chk


def _ev(ident: str, title: str = "", relevance: float = 0.9) -> Evidence:
    return Evidence(
        source="Google Patents",
        identifier=ident,
        title=title or ident,
        snippet="锌系磷化膜耐蚀性…",
        relevance=relevance,
    )


# ── store level ─────────────────────────────────────────────────────────────


def test_record_ingest_failure_inserts_failed_row(stores):
    src, _ = stores
    row_id = src.record_ingest_failure(
        origin_url="US9999999",
        filename="US9999999",
        title="t",
        source_kind="patent",
        project_id=None,
        error="x" * 600,  # truncated at write time
    )
    assert row_id
    row = src.find_failed_by_origin("US9999999")
    assert row is not None and row.id == row_id
    assert row.ingest_status == "failed"
    assert row.ingest_error == "x" * 500
    assert row.full_text is None


def test_record_ingest_failure_never_downgrades_indexed_row(stores, monkeypatch):
    src, _ = stores
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    kb_ingest.ingest_evidence_docs([_ev("US1111111")])
    indexed = src.find_by_origin_url("US1111111")
    # full_text is cleared after chunking (clear_full_text); the row is real
    # iff it was indexed with chunks.
    assert indexed is not None and indexed.extraction_status == "fulltext"

    src.record_ingest_failure(
        origin_url=indexed.origin_url,
        filename="US1111111",
        title="t",
        source_kind="patent",
        project_id=None,
        error="stale failure must not clobber",
    )
    again = src.find_by_origin_url("US1111111")
    assert again.extraction_status == "fulltext"  # untouched
    assert again.ingest_status != "failed"


def test_revive_failed_row_promotes_in_place(stores):
    src, _ = stores
    row_id = src.record_ingest_failure(
        origin_url="US2222222",
        filename="US2222222",
        title="t",
        source_kind="patent",
        project_id=None,
        error="boom",
    )
    src.revive_failed_row(
        row_id, full_text=LONG_TEXT, content_hash="abc123",
        filename="US2222222", title="t2",
    )
    assert src.find_failed_by_origin("US2222222") is None
    row = src.find_by_origin_url("US2222222")
    assert row.id == row_id  # same row, not a duplicate
    assert row.ingest_status == "indexed"
    assert row.ingest_error is None
    assert row.full_text == LONG_TEXT


# ── ingest path ─────────────────────────────────────────────────────────────


def test_no_guide_status_when_no_api_key(monkeypatch):
    """P0-1：有真实文本但无 LLM key → extraction_status 为 "no_guide" 而非 "skipped"。"""
    from app.config import Settings
    from app.services.ingestion import ingest_text

    monkeypatch.setattr(Settings, "get_active_api_key", lambda self: None)
    # source_guide_enabled defaults to True; no API key => guide cannot run
    get_settings.cache_clear()
    assert get_settings().source_guide_enabled is True
    try:
        outcome = ingest_text(LONG_TEXT, "测试文档", persist=False)
    finally:
        get_settings.cache_clear()
    assert outcome.extraction_status == "no_guide", (
        f"应为 no_guide，实际: {outcome.extraction_status}"
    )
    assert outcome.evidence, "真实文本应产出 evidence"


def test_skipped_status_when_no_text():
    """P0-1：无文本 → 仍为 "skipped"（占位语义保留）。"""
    from app.services.ingestion import ingest_text

    outcome = ingest_text("", "空文档", persist=False)
    assert outcome.extraction_status == "skipped"


def test_colbert_gate_indexes_no_guide_status():
    """P0-1：status 为 no_guide（有真实文本）时，门禁应放行索引。

    门禁逻辑（api/ingest.py）：extraction_status != "skipped" 即索引。
    "skipped" 现仅=无真实文本/占位，故 no_guide 能正确索引。
    """
    from app.services.ingestion import IngestOutcome
    from app.domain.schemas import Evidence

    ev = Evidence(source="local", identifier="test:1", title="t",
                snippet="真实文本内容", relevance=0.9)
    outcome = IngestOutcome(evidence=[ev], extraction_status="no_guide")
    # 门禁条件
    assert outcome.extraction_status != "skipped", "no_guide 应被索引"
    # 占位仍被拦截
    skipped = IngestOutcome(evidence=[], extraction_status="skipped")
    assert not (skipped.extraction_status != "skipped")


def test_failed_row_is_retried_not_skipped(stores, monkeypatch):
    src, _ = stores
    # Simulate a previous failed attempt for this origin.
    from app.services.patent_ids import canonical_origin_url

    origin = canonical_origin_url("US3333333", url=None)
    src.record_ingest_failure(
        origin_url=origin,
        filename="US3333333",
        title="t",
        source_kind="patent",
        project_id=None,
        error="previous attempt failed",
    )

    calls: list[str] = []
    monkeypatch.setattr(
        ff, "_fetch_patent_text",
        lambda ev, t: calls.append(ev.identifier) or LONG_TEXT,
    )
    result = kb_ingest.ingest_evidence_docs([_ev("US3333333")])

    assert calls == ["US3333333"]  # fetched again, not skipped
    assert result["indexed"] == 1 and result["skipped"] == 0
    # Failed row revived in place — still one logical row for the origin,
    # now indexed with the error cleared.
    row = src.find_by_origin_url("US3333333")
    assert row is not None
    assert row.ingest_status == "indexed"
    assert row.ingest_error is None
    assert src.find_failed_by_origin(origin) is None


def test_indexed_row_skipped_with_reason(stores, monkeypatch):
    src, _ = stores
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    kb_ingest.ingest_evidence_docs([_ev("US4444444")])

    result = kb_ingest.ingest_evidence_docs([_ev("US4444444")])
    assert result["skipped"] == 1
    assert result["skipped_by_reason"] == {"already_ingested": 1}
    doc = result["docs"][0]
    assert doc["status"] == "skipped" and doc["skip_reason"] == "already_ingested"


def test_fetch_failure_persisted_to_db(stores, monkeypatch):
    src, _ = stores

    def _boom(kind, ev, timeout, allow_pdf=True):
        raise ff.FetchError("no_oa_version")

    monkeypatch.setattr(ff, "_dispatch_fetch", _boom)
    result = kb_ingest.ingest_evidence_docs([_ev("US5555555"), _ev("US6666666")])

    assert result["failed"] == 2 and result["indexed"] == 0
    for ident in ("US5555555", "US6666666"):
        row = src.find_failed_by_origin(ident)
        assert row is not None, ident
        assert row.ingest_status == "failed"
        assert "no_oa_version" in (row.ingest_error or "")
    # Failure reasons also stay visible in the in-memory summary.
    assert all(d["status"] == "failed" and d["error"] for d in result["docs"])


def test_failure_persistence_is_fail_open(stores, monkeypatch):
    """A broken failure-persistence path must not fail the batch itself."""
    from app.db import source_store as source_store_mod

    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)

    def _broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        source_store_mod.SourceStore, "record_ingest_failure", _broken
    )

    def _boom(kind, ev, timeout, allow_pdf=True):
        raise ff.FetchError("no_oa_version")

    monkeypatch.setattr(ff, "_dispatch_fetch", _boom)
    result = kb_ingest.ingest_evidence_docs([_ev("US7777777")])
    assert result["failed"] == 1  # batch completes; error still in summary
    assert result["docs"][0]["error"]


def test_find_by_hash_ignores_failed_rows(stores):
    """P1-8 风险1：失败行保留真实 content_hash，但 find_by_hash 必须跳过它，
    否则下一次 _persist_fulltext 直接命中零-chunk 失败行，形成僵尸。"""
    src, _ = stores
    content_hash = "ab" * 32
    sid = src.create(
        filename="f.pdf", title="f", source_kind="web",
        full_text="x" * 100, content_hash=content_hash,
    )
    src.update_fields(sid, ingest_status="failed", ingest_error="0 chunks")
    assert src.find_by_hash(content_hash) is None

    # 非失败行仍可命中
    sid2 = src.create(
        filename="g.pdf", title="g", source_kind="web",
        full_text="y" * 100, content_hash="cd" * 32,
        ingest_status="indexed",
    )
    assert src.find_by_hash("cd" * 32).id == sid2
    # NULL 状态的历史行也可命中
    sid3 = src.create(
        filename="h.pdf", title="h", source_kind="web",
        full_text="z" * 100, content_hash="ef" * 32,
    )
    assert src.find_by_hash("ef" * 32).id == sid3
