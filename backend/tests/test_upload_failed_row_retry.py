"""P1-2: a failed-ingest row is a retry candidate, never a "duplicate".

Before: ``find_by_origin_url`` returned failed rows too, so

* a re-upload of a file whose first ingest failed was skipped as "已有资料一致"
  and could never be re-ingested (the row holds no chunks);
* the one-click ``ingest_single_evidence`` answered ``ok/skipped`` for it;
* the research ``_filter_unindexed_external`` kept sources whose full text was
  never obtained (the failed row made them look ingested);
* a retry that did go through created a second row for the same origin;
* "every chunk already exists elsewhere" (L1 exact dedup) was recorded as a
  bare ``index_source produced 0 chunks`` failure with no hint for the user.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import Evidence
from app.services import fulltext_fetcher as ff
from app.services import ingestion, kb_ingest

LONG_TEXT = "# 防腐蚀专利\n\n" + "环氧树脂与磷酸锌协同防腐蚀机理研究。" * 60


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KB_INGEST_AUTO", "true")
    monkeypatch.setenv("FORMUMIND_TASK_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("FORMUMIND_DEEPSEEK_API_KEY", "")
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


def _ev(ident: str) -> Evidence:
    return Evidence(
        source="Google Patents",
        identifier=ident,
        title=ident,
        snippet="锌系磷化膜耐蚀性…",
        relevance=0.9,
    )


def _fail(src, origin: str, error: str = "boom") -> str:
    row_id = src.record_ingest_failure(
        origin_url=origin,
        filename="f",
        title="t",
        source_kind="local",
        project_id=None,
        error=error,
    )
    assert row_id
    return row_id


# ── store-level lookup ──────────────────────────────────────────────────────


def test_origin_lookup_can_exclude_failed_rows(stores):
    src, _ = stores
    failed_id = _fail(src, "upload:sha256:aaa")

    assert src.find_by_origin_url("upload:sha256:aaa").id == failed_id  # default: unchanged
    assert src.find_by_origin_url("upload:sha256:aaa", include_failed=False) is None
    assert src.find_by_origin_urls(["upload:sha256:aaa"], include_failed=False) is None

    live = src.create(
        filename="g", title="g", source_kind="local", full_text="x" * 50,
        content_hash="h", origin_url="upload:sha256:bbb", ingest_status="indexed",
    )
    assert src.find_by_origin_url("upload:sha256:bbb", include_failed=False).id == live


def test_revive_failed_row_carries_upload_fields(stores):
    src, _ = stores
    row_id = _fail(src, "upload:sha256:ccc")
    src.revive_failed_row(
        row_id, full_text="y" * 60, content_hash="h2", filename="a.txt", title="a",
        extraction_status="ok", parser="text",
    )
    row = src.get(row_id)
    assert (row.extraction_status, row.parser, row.ingest_status) == ("ok", "text", "indexed")
    assert row.ingest_error is None


# ── upload task ─────────────────────────────────────────────────────────────


def _run_upload(tmp_path, monkeypatch, name: str, content: bytes) -> dict:
    from app.services import colbert_store
    from app.worker import tasks

    monkeypatch.setattr(colbert_store, "index_evidence", lambda ev: None)
    f = tmp_path / name
    f.write_bytes(content)
    return tasks._file_ingest_impl("t-" + name, {"files": [{"name": name, "path": str(f)}], "dir": None})


def test_reupload_after_failed_ingest_is_retried_and_revives_the_row(stores, tmp_path, monkeypatch):
    import app.services.kb_index as kb_index

    src, chk = stores
    content = LONG_TEXT.encode("utf-8")

    # 1st attempt: indexing yields nothing → row is marked failed.
    real_index = kb_index.index_source
    monkeypatch.setattr(kb_index, "index_source", lambda *a, **k: 0)
    first = _run_upload(tmp_path, monkeypatch, "spec.txt", content)
    assert first["duplicates"] == []
    first_id = first["source_id"]
    assert first_id and src.get(first_id).ingest_status == "failed"

    # 2nd attempt, same bytes, indexing works now: NOT skipped as a duplicate…
    monkeypatch.setattr(kb_index, "index_source", real_index)
    second = _run_upload(tmp_path, monkeypatch, "spec.txt", content)
    assert second["duplicates"] == []
    assert second["files_processed"] == 1
    # …and the failed row was revived in place (one row per origin).
    assert second["source_id"] == first_id
    row = src.get(first_id)
    assert row.ingest_status == "indexed" and row.ingest_error is None
    assert chk.get_by_source(first_id), "revived row must have chunks"

    # 3rd attempt: now it really is a duplicate, and the result says of what.
    third = _run_upload(tmp_path, monkeypatch, "spec.txt", content)
    assert third["duplicates"] == ["spec.txt"]
    assert third["duplicate_source_ids"] == {"spec.txt": first_id}
    assert third["files_processed"] == 0


def test_all_chunks_duplicate_is_reported_to_the_user(stores, tmp_path, monkeypatch):
    src, _ = stores
    first = ingestion.ingest_file("a.txt", LONG_TEXT.encode(), origin_url="upload:sha256:one")
    assert first.source_id and first.extraction_status != "failed"

    # Different bytes (extra blank lines) but identical chunk text/provenance.
    second = ingestion.ingest_file(
        "b.txt", (LONG_TEXT + "\n\n\n").encode(), origin_url="upload:sha256:two"
    )
    assert second.extraction_status == "failed"
    assert any("重复" in w for w in second.warnings), second.warnings
    row = src.get(second.source_id)
    assert row.ingest_status == "failed"
    assert "重复" in (row.ingest_error or "")

    # A genuine zero-chunk failure keeps the generic reason and message.
    import app.services.kb_index as kb_index

    monkeypatch.setattr(kb_index, "index_source", lambda *a, **k: 0)
    third = ingestion.ingest_file(
        "c.txt", ("# 另一份文档\n\n" + "完全不同的内容。" * 80).encode(), origin_url="upload:sha256:three"
    )
    assert third.extraction_status == "failed"
    assert any("入库失败" in w for w in third.warnings), third.warnings
    assert "0 chunks" in (src.get(third.source_id).ingest_error or "")


# ── one-click ingest + research filter ──────────────────────────────────────


def test_ingest_single_retries_a_failed_row_instead_of_reporting_skipped(stores, monkeypatch):
    src, _ = stores
    from app.services.patent_ids import canonical_origin_url

    origin = canonical_origin_url("US7777777", url=None)
    failed_id = _fail(src, origin, "previous attempt failed")
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)

    result = kb_ingest.ingest_single_evidence(_ev("US7777777"))

    assert result["status"] == "indexed", result
    assert result["source_id"] == failed_id  # revived in place
    assert src.get(failed_id).ingest_status == "indexed"


def test_ingest_single_still_skips_an_indexed_row(stores, monkeypatch):
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    first = kb_ingest.ingest_single_evidence(_ev("US8888888"))
    assert first["status"] == "indexed"
    again = kb_ingest.ingest_single_evidence(_ev("US8888888"))
    assert again["status"] == "skipped" and again["source_id"] == first["source_id"]


def test_research_filter_drops_sources_whose_ingest_failed(stores):
    from app.pipeline.research_graph import _filter_unindexed_external

    src, _ = stores
    url_failed = "https://example.org/paper-that-never-downloaded"
    url_ok = "https://example.org/paper-ingested"
    _fail(src, url_failed, "全文获取失败")
    src.create(
        filename="ok", title="ok", source_kind="web", full_text="z" * 60,
        content_hash="hh", origin_url=url_ok, ingest_status="indexed",
    )
    ev = lambda ident: Evidence(  # noqa: E731
        source="web", identifier=ident, title=ident, snippet="s", relevance=0.5
    )
    kept = _filter_unindexed_external([ev(url_failed), ev(url_ok), ev("seed:local-1")])
    assert [e.identifier for e in kept] == [url_ok, "seed:local-1"]
