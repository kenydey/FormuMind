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


# ── every searched source gets ingested ──────────────────────────────────────


def _many(n: int) -> list[Evidence]:
    return [
        Evidence(
            title=f"文献 {i}", snippet="摘要" * 20, source="patents",
            identifier=f"CN10{i:05d}A", url=f"https://patents.example.com/CN10{i:05d}A",
            relevance=0.9,
        )
        for i in range(n)
    ]


def test_no_doc_cap_by_default(monkeypatch):
    """The reported symptom: 340 search hits produced 24 ingested documents.

    "Searched but not stored" reads as data loss to whoever ran the search, so
    the default must be to take everything fetchable.
    """
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    targets = kb_ingest.select_ingest_targets(_many(340))
    assert len(targets) == 340


def test_an_explicit_cap_is_still_honoured(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 24, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    assert len(kb_ingest.select_ingest_targets(_many(340))) == 24
    # An explicit argument still wins over the setting.
    assert len(kb_ingest.select_ingest_targets(_many(340), max_docs=5)) == 5


def test_fetches_run_concurrently_but_indexing_stays_serial(monkeypatch):
    """Fetching 340 documents one at a time at a 20 s timeout is over an hour.
    Indexing must not be parallelised with it: it writes SQLite, and each fetch
    already ran the PDF parser cascade whose peak is what bounds a small host.
    """
    import threading

    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_workers", 4, raising=False)

    fetch_threads: set[int] = set()
    index_threads: set[int] = set()
    index_concurrent = []
    index_lock = threading.Lock()
    inside = {"n": 0}

    def fake_fetch(ev, kind, timeout, emit, doc):
        fetch_threads.add(threading.get_ident())
        return "全文内容"

    def fake_index(text, ev, kind, emit, doc, project_id=None):
        index_threads.add(threading.get_ident())
        with index_lock:
            inside["n"] += 1
            index_concurrent.append(inside["n"])
        doc.update(status="indexed", source_id=f"src-{ev.identifier}")
        with index_lock:
            inside["n"] -= 1

    monkeypatch.setattr(kb_ingest, "_fetch_one", fake_fetch)
    monkeypatch.setattr(kb_ingest, "_index_one", fake_index)

    summary = kb_ingest.ingest_evidence_docs(_many(12))

    assert summary["total"] == 12
    assert summary["indexed"] == 12
    assert len(fetch_threads) > 1, "fetching must use the pool"
    assert max(index_concurrent) == 1, "indexing must never overlap"


def test_a_single_worker_still_works(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_workers", 1, raising=False)
    monkeypatch.setattr(kb_ingest, "_fetch_one", lambda *a: "全文")
    monkeypatch.setattr(
        kb_ingest, "_index_one",
        lambda text, ev, kind, emit, doc, project_id=None: doc.update(
            status="indexed", source_id="s"
        ),
    )
    assert kb_ingest.ingest_evidence_docs(_many(3))["indexed"] == 3


def test_one_failing_fetch_does_not_stop_the_queue(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_workers", 3, raising=False)

    def flaky(ev, kind, timeout, emit, doc):
        if ev.identifier.endswith("00002A"):
            raise RuntimeError("network exploded")
        return "全文"

    monkeypatch.setattr(kb_ingest, "_fetch_one", flaky)
    monkeypatch.setattr(
        kb_ingest, "_index_one",
        lambda text, ev, kind, emit, doc, project_id=None: doc.update(
            status="indexed", source_id="s"
        ),
    )
    summary = kb_ingest.ingest_evidence_docs(_many(5))
    assert summary["indexed"] == 4
    assert summary["failed"] == 1


def test_the_whole_queue_is_announced_before_any_fetch(monkeypatch):
    """The progress bar shows N/M from the start, so M must be the real total —
    not something that grows as documents are discovered."""
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_workers", 2, raising=False)
    announced: list[str] = []
    fetched: list[str] = []

    def fake_fetch(ev, kind, timeout, emit, doc):
        fetched.append(ev.identifier)
        return None

    monkeypatch.setattr(kb_ingest, "_fetch_one", fake_fetch)
    kb_ingest.ingest_evidence_docs(
        _many(6), status_cb=lambda meta: announced.append(meta["identifier"])
    )
    assert len(set(announced)) == 6
    assert len(announced[:6]) == 6, "all six announced before work started"


def test_chinese_patents_are_fetchable(monkeypatch):
    """The second cause of "searched but not stored", and the worse one.

    `_PATENT_RE` was `^(US|EP)\\d+`, so a CN/JP/WO publication classified as
    un-fetchable and never entered the ingest queue at all — no cap involved, no
    failed row in the UI, just absent. `fetch_patent_pdf` falls back to Google
    Patents, which serves those offices, so the restriction never matched what
    was actually retrievable.
    """
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    rows = [
        Evidence(title=t, snippet="摘要", source="patents", identifier=i, relevance=0.9)
        for i, t in [
            ("CN102345678A", "中国专利"),
            ("JP2001234567A", "日本专利"),
            ("WO2020123456A1", "PCT 申请"),
            ("US1234567B2", "美国专利"),
        ]
    ]
    targets = kb_ingest.select_ingest_targets(rows)
    assert [ev.identifier for ev, _ in targets] == [r.identifier for r in rows]
    assert all(kind == "patent" for _, kind in targets)


def test_synthetic_and_chunk_level_ids_are_still_rejected():
    """Widening the patent match must not start fetching things that are not
    documents — chunk fragments and hash-derived placeholders have nothing behind
    them."""
    for ident in ("chemlit:ab12", "chemweb:00ff", "CN102345678A#p3", "P1", ""):
        ev = Evidence(
            title="t", snippet="s", source="patents", identifier=ident, relevance=0.9
        )
        assert ff.classify(ev) is None, ident


# ── no time limit on a long build ────────────────────────────────────────────


def test_stream_deadline_is_configurable_and_generous():
    """A 340-document ingest runs for tens of minutes. The Redis path's hardcoded
    one hour and the fallback's 120 s could both cut a healthy job off."""
    assert get_settings().task_stream_timeout_s >= 3600


def test_poll_fallback_does_not_report_failure_on_deadline(monkeypatch):
    """It used to yield a FAILED event saying "polling timed out", which told the
    UI a running task had died. The task has not failed — the connection has just
    been open long enough — so the stream closes and the client falls back to
    polling for the real state.
    """
    import asyncio

    from app.api import tasks as tasks_api

    monkeypatch.setattr(get_settings(), "task_stream_timeout_s", 0.05, raising=False)
    monkeypatch.setattr(tasks_api, "get_task_meta", lambda tid: None)
    monkeypatch.setattr(tasks_api, "_terminal_event_from_disk", lambda tid: None)

    async def drain():
        return [ev async for ev in tasks_api._poll_until_terminal("t-1")]

    assert asyncio.run(drain()) == [], "deadline must close the stream, not fail it"


# ── timing instrumentation is actually wired in ──────────────────────────────


def test_ingest_emits_per_document_and_batch_timing(stores, monkeypatch, caplog):
    """The instrumentation must fire from a real ingest, not just in isolation.

    Every previous performance question about this pipeline was answered by
    guessing, and guessing was wrong twice: the patent tier looked like a slow
    parser and was three dead endpoints, the arXiv tier looked like a slow
    parser and was OCR on figure pages.
    """
    import logging

    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")

    kb_ingest.ingest_evidence_docs([_ev("US1234567"), _ev("US7654321")])

    messages = [r.getMessage() for r in caplog.records]
    docs = [m for m in messages if "kb_ingest doc" in m]
    batches = [m for m in messages if "kb_ingest batch" in m]

    assert len(docs) == 2, messages
    assert all("indexed" in m for m in docs)
    assert any("[patent]" in m for m in docs)
    # The phases that matter for attribution are present.
    assert all("download=" in m for m in docs)
    assert any("chunk=" in m for m in docs)
    assert len(batches) == 1
    assert "2 docs" in batches[0] and "indexed=2" in batches[0]


def test_failed_documents_are_timed_too(stores, monkeypatch, caplog):
    """A batch that is mostly failures is the case worth measuring.

    That was literally the patent situation: every document burned two
    timeouts and produced nothing, so the time was all in the failures.
    """
    import logging

    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: None)
    caplog.set_level(logging.INFO, logger="app.services.ingest_timing")

    kb_ingest.ingest_evidence_docs([_ev("US1234567")])

    messages = [r.getMessage() for r in caplog.records]
    assert any("kb_ingest doc US1234567" in m and "failed" in m for m in messages), messages
    assert any("failed=1" in m for m in messages if "batch" in m)


# ── fetch and index are pipelined ────────────────────────────────────────────


def test_indexing_starts_before_the_last_fetch_finishes(stores, monkeypatch):
    """`ex.map` yields in submission order, so one slow head-of-queue download
    held back every document behind it and nothing was indexed until the final
    fetch returned. It also kept every fetched full text in memory at once.

    The observable claim is ordering: with the first document deliberately slow,
    at least one *other* document must reach indexing before it does.
    """
    import threading
    import time as _time

    slow_ident = "US1000001"
    first_indexed: list[str] = []
    fetch_done: dict[str, float] = {}
    lock = threading.Lock()

    def fake_fetch(ev, t):
        if ev.identifier == slow_ident:
            _time.sleep(0.6)
        with lock:
            fetch_done[ev.identifier] = _time.perf_counter()
        return LONG_TEXT

    real_index = kb_ingest._index_one

    def spy_index(text, ev, kind, emit, doc, **kw):
        with lock:
            first_indexed.append(ev.identifier)
        return real_index(text, ev, kind, emit, doc, **kw)

    monkeypatch.setattr(ff, "_fetch_patent_text", fake_fetch)
    monkeypatch.setattr(kb_ingest, "_index_one", spy_index)
    monkeypatch.setenv("FORMUMIND_KB_INGEST_WORKERS", "3")
    get_settings.cache_clear()

    evs = [_ev(slow_ident)] + [_ev(f"US200000{i}") for i in range(1, 4)]
    result = kb_ingest.ingest_evidence_docs(evs)

    assert result["indexed"] == 4
    assert first_indexed, "nothing was indexed"
    assert first_indexed[0] != slow_ident, (
        "the slow head-of-queue document was indexed first — fetches are still "
        f"being consumed in submission order: {first_indexed}"
    )


def test_pipelining_preserves_per_document_status(stores, monkeypatch):
    """Out-of-order completion must not scramble which status lands on which doc."""
    def fake_fetch(ev, t):
        # Fail exactly one document; its neighbours must stay unaffected.
        return None if ev.identifier == "US1234567" else LONG_TEXT

    monkeypatch.setattr(ff, "_fetch_patent_text", fake_fetch)
    monkeypatch.setenv("FORMUMIND_KB_INGEST_WORKERS", "3")
    get_settings.cache_clear()

    evs = [_ev("US1234567"), _ev("US7654321"), _ev("US9999999")]
    result = kb_ingest.ingest_evidence_docs(evs)

    by_id = {d["identifier"]: d["status"] for d in result["docs"]}
    assert by_id["US1234567"] == "failed"
    assert by_id["US7654321"] == "indexed"
    assert by_id["US9999999"] == "indexed"


# ── the executor must not enforce a limit the rest of the stack disowned ─────


def test_celery_time_limits_leave_room_for_a_large_build():
    """These were 600 / 900 s while everything else had been told a build takes
    as long as it takes — no client wall clock, a 6 h SSE deadline, and
    kb_ingest_max_docs=0. Celery was the one layer still killing the job."""
    from app.worker.celery_app import celery_app

    s = get_settings()
    soft = celery_app.conf.task_soft_time_limit
    hard = celery_app.conf.task_time_limit

    assert soft == s.celery_soft_time_limit_s
    assert hard == s.celery_hard_time_limit_s
    assert soft > 1800, "a few hundred documents does not fit in half an hour"
    assert soft < hard, "the soft limit must fire first so the task can report"
    # The stream has to outlive the task, or the client stops watching a task
    # that is still running — the false "构建中断" this whole area suffered from.
    assert hard < s.task_stream_timeout_s


def test_soft_time_limit_reports_which_knob_to_turn(stores, monkeypatch):
    """A bare exception name tells the operator nothing actionable."""
    from celery.exceptions import SoftTimeLimitExceeded

    from app.services import kb_ingest as kb_ingest_mod
    from app.worker import tasks as tasks_mod

    def boom(*a, **kw):
        raise SoftTimeLimitExceeded()

    # `tasks` imports kb_ingest inside the function, so patch it at the source.
    monkeypatch.setattr(kb_ingest_mod, "ingest_evidence_docs", boom)

    with pytest.raises(SoftTimeLimitExceeded):
        tasks_mod._kb_ingest_impl("t-soft", {"evidence": [_ev("US1234567").model_dump()]})

    from app.worker.task_progress import get_task_result

    result = get_task_result("t-soft") or {}
    assert "FORMUMIND_CELERY_SOFT_TIME_LIMIT_S" in result.get("error", "")
    assert result.get("soft_time_limit_s") == get_settings().celery_soft_time_limit_s
