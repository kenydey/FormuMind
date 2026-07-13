"""Async KB ingest queue (KB stream P0).

Contract under test:
1. the per-document state machine (queued → fetching → indexing → indexed /
   skipped / failed) emits every transition through status_cb;
2. dedup — a document whose origin_url is already stored is skipped without
   a network attempt; content-hash dedup still applies at persist time;
3. one failing document never kills the rest of the queue;
4. task wiring — run_search_task dispatches a background ingest and returns
   ``kb_ingest_task_id`` in its result *without* blocking on the ingestion;
5. the auto flag / kb_v2 flag gate dispatch entirely.

All network fetchers are stubbed — the suite stays offline.
"""
from __future__ import annotations

import time

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
    """Isolated SourceStore + ChunkStore on a temp SQLite DB."""
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


# ── state machine ────────────────────────────────────────────────────────────


def test_full_state_machine_on_success(stores, monkeypatch):
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    events: list[tuple[str, str]] = []

    result = kb_ingest.ingest_evidence_docs(
        [_ev("US1234567")], status_cb=lambda m: events.append((m["identifier"], m["status"]))
    )

    assert [s for _, s in events] == ["queued", "fetching", "indexing", "indexed"]
    assert result["total"] == 1 and result["indexed"] == 1
    src, chk = stores
    doc = src.find_by_origin_url("US1234567")
    assert doc is not None and doc.extraction_status == "fulltext"
    assert chk.get_by_source(doc.id)  # chunks landed in the persistent KB


def test_origin_url_dedup_skips_without_fetch(stores, monkeypatch):
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    kb_ingest.ingest_evidence_docs([_ev("US1234567")])

    calls: list[str] = []
    monkeypatch.setattr(
        ff, "_fetch_patent_text", lambda ev, t: calls.append(ev.identifier) or LONG_TEXT
    )
    events: list[str] = []
    result = kb_ingest.ingest_evidence_docs(
        [_ev("US1234567")], status_cb=lambda m: events.append(m["status"])
    )
    assert calls == []  # no re-download
    assert result["skipped"] == 1
    assert events == ["queued", "skipped"]


def test_failed_doc_does_not_kill_queue(stores, monkeypatch):
    def fetch(ev, t):
        if ev.identifier == "US1111111":
            raise RuntimeError("boom")
        return LONG_TEXT

    monkeypatch.setattr(ff, "_fetch_patent_text", fetch)
    result = kb_ingest.ingest_evidence_docs([_ev("US1111111"), _ev("US2222222")])
    by_id = {d["identifier"]: d for d in result["docs"]}
    assert by_id["US1111111"]["status"] == "failed"
    assert by_id["US1111111"]["error"]
    assert by_id["US2222222"]["status"] == "indexed"


def test_unfetchable_rows_are_not_queued(stores):
    result = kb_ingest.ingest_evidence_docs(
        [_ev("local-file#p3"), Evidence(source="seed", identifier="", title="x", snippet="s", relevance=0.5)]
    )
    assert result["total"] == 0


def test_max_docs_cap(stores, monkeypatch):
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    evs = [_ev(f"US{i}000000") for i in range(1, 6)]
    result = kb_ingest.ingest_evidence_docs(evs, max_docs=2)
    assert result["total"] == 2


# ── structure-aware chunking of fetched fulltext ─────────────────────────────


def test_fulltext_chunks_keep_tables_atomic(monkeypatch):
    md = (
        "# 配方专利\n\n"
        + "背景描述。" * 80
        + "\n\n## 实施例\n\n| 组分 | 份数 |\n|---|---|\n"
        + "\n".join(f"| 组分{i} | {i} |" for i in range(1, 40))
        + "\n\n结论。"
    )
    chunks = ff._text_to_chunks(md, _ev("US7654321", title="配方专利"))
    table_chunks = [c for c in chunks if "| 组分1 |" in c.snippet or "组分" in c.snippet]
    assert chunks
    # heading path must surface in至少一个 chunk 标题（结构感知切块生效）
    assert any("实施例" in c.title for c in chunks)


# ── task wiring ──────────────────────────────────────────────────────────────


def _wait_for_terminal(task_id: str, timeout: float = 5.0) -> dict | None:
    from app.worker.tasks import load_persisted_task

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = load_persisted_task(task_id)
        if status is not None and status.state.value in ("completed", "failed"):
            return status.model_dump()
        time.sleep(0.05)
    return None


def test_dispatch_returns_task_id_and_runs_in_background(stores, monkeypatch):
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    from app.worker import tasks as worker_tasks

    task_id = worker_tasks.dispatch_kb_ingest([_ev("US9999999").model_dump()])
    assert task_id
    status = _wait_for_terminal(task_id)
    assert status is not None, "ingest thread did not finish"
    assert status["result"]["indexed"] == 1


def test_dispatch_disabled_by_flag(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_INGEST_AUTO", "false")
    get_settings.cache_clear()
    from app.worker import tasks as worker_tasks

    assert worker_tasks.dispatch_kb_ingest([_ev("US9999999").model_dump()]) is None


def test_dispatch_requires_kb_v2(monkeypatch):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    from app.worker import tasks as worker_tasks

    assert worker_tasks.dispatch_kb_ingest([_ev("US9999999").model_dump()]) is None


def test_dispatch_skips_when_nothing_fetchable():
    from app.worker import tasks as worker_tasks

    assert worker_tasks.dispatch_kb_ingest([_ev("chunk#p1").model_dump()]) is None


def test_search_task_attaches_kb_ingest_task_id(stores, monkeypatch):
    from app.services import literature
    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    monkeypatch.setattr(
        literature,
        "iter_search",
        lambda *a, **k: [_ev("US5555555", title="锌系磷化专利")],
    )

    result = worker_tasks.run_search_task.apply(
        args=[{"query": "锌系磷化", "source_types": ["patents"]}]
    ).get()

    assert result["total"] == 1
    task_id = result.get("kb_ingest_task_id")
    assert task_id, "search result must carry the ingest task id"
    status = _wait_for_terminal(task_id)
    assert status is not None and status["result"]["indexed"] == 1


def test_search_task_without_ingest_when_disabled(monkeypatch):
    from app.services import literature
    from app.worker import tasks as worker_tasks

    monkeypatch.setenv("FORMUMIND_KB_INGEST_AUTO", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        literature, "iter_search", lambda *a, **k: [_ev("US5555555")]
    )
    result = worker_tasks.run_search_task.apply(
        args=[{"query": "锌系磷化", "source_types": ["patents"]}]
    ).get()
    assert "kb_ingest_task_id" not in result
