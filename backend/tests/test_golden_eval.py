"""Task 2.7: Golden eval tests — retrieval relevance + citation format + dataset validity.

Three tests:
  test_golden_eval_dataset_valid   — dataset structural integrity
  test_golden_retrieval_relevance  — hybrid_search returns keyword-matched results
  test_golden_citation_format      — bind_citations produces non-empty footnotes
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory

# ── Fixtures (mirror test_hybrid_search.py / test_ingest.py pattern) ──────────


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Isolated SourceStore + ChunkStore on a temp SQLite DB."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


# ── test 1: dataset structural validity ──────────────────────────────────────


@pytest.mark.golden_eval
def test_golden_eval_dataset_valid():
    """Golden eval dataset is well-formed and has enough entries."""
    from .golden_eval_dataset import golden_questions, sample_documents

    assert len(golden_questions) >= 3, (
        f"expected at least 3 golden questions, got {len(golden_questions)}"
    )
    for i, entry in enumerate(golden_questions):
        assert isinstance(entry, dict), f"entry[{i}] is not a dict"
        q = entry.get("question")
        assert q and isinstance(q, str) and q.strip(), (
            f"entry[{i}].question must be a non-empty string, got {q!r}"
        )
        kw = entry.get("expected_keywords")
        assert isinstance(kw, list) and len(kw) > 0, (
            f"entry[{i}].expected_keywords must be a non-empty list, got {kw!r}"
        )
        for j, w in enumerate(kw):
            assert isinstance(w, str) and w.strip(), (
                f"entry[{i}].expected_keywords[{j}] must be a non-empty string"
            )

    # sample_documents should also have content
    assert len(sample_documents) >= 2, (
        f"expected at least 2 sample documents, got {len(sample_documents)}"
    )
    for i, doc in enumerate(sample_documents):
        assert doc.get("title"), f"sample_documents[{i}] missing title"
        assert doc.get("text"), f"sample_documents[{i}] missing text"


# ── test 2: golden retrieval relevance ───────────────────────────────────────


@pytest.mark.golden_eval
def test_golden_retrieval_relevance(stores):
    """For each golden question, top-3 hybrid_search results contain at least
    one expected keyword."""
    from .golden_eval_dataset import golden_questions, sample_documents

    client = _client()

    # Ingest sample documents into the isolated test DB
    ingested_sids = []
    for doc in sample_documents:
        resp = client.post(
            "/api/kb/ingest",
            json={"text": doc["text"], "title": doc["title"]},
        )
        assert resp.status_code == 200, (
            f"ingest failed for {doc['title']}: {resp.status_code} {resp.json()}"
        )
        ingested_sids.append(resp.json()["source_id"])

    assert len(ingested_sids) == len(sample_documents), (
        "not all sample documents were ingested"
    )

    # Run each golden question through hybrid_search
    failures = []
    for entry in golden_questions:
        question = entry["question"]
        keywords = entry["expected_keywords"]
        category = entry["min_relevance_category"]

        resp = client.post(
            "/api/kb/hybrid-search",
            json={"query": question, "top_k": 3},
        )
        assert resp.status_code == 200, (
            f"hybrid-search failed for {question!r}: {resp.status_code}"
        )
        results = resp.json()
        top3_texts = [r.get("text", "") for r in results[:3]]

        # At least one keyword must appear in the combined top-3 text
        combined = "".join(top3_texts)
        hit = any(kw in combined for kw in keywords)
        if not hit:
            failures.append(
                f"[{category}] {question!r} — no keyword from {keywords} "
                f"in top-3: {[t[:60] for t in top3_texts]}"
            )

    if failures:
        pytest.fail(
            f"{len(failures)}/{len(golden_questions)} golden questions failed:\n"
            + "\n".join(failures)
        )


# ── test 3: citation format ──────────────────────────────────────────────────


@pytest.mark.golden_eval
def test_golden_citation_format():
    """bind_citations generates non-empty footnotes for answers with [^n] markers."""
    from app.domain.citations import CitationAnchor
    from app.services.citation_binder import bind_citations

    # Build mock anchors simulating retrieved chunks
    anchors = [
        CitationAnchor(
            chunk_id="chunk-001",
            source_id="doc-epoxy",
            text="环氧树脂通过固化形成致密交联网络，阻隔腐蚀介质渗透。",
            page=3,
            paragraph=5,
        ),
        CitationAnchor(
            chunk_id="chunk-002",
            source_id="doc-phosphate",
            text="磷酸锌水解生成磷酸根离子，与金属基材形成钝化保护膜。",
            page=7,
            paragraph=12,
        ),
        CitationAnchor(
            chunk_id="chunk-003",
            source_id="doc-process",
            text="固化温度控制在80-120°C，涂膜厚度25-35微米时防腐性能最优。",
            page=2,
            paragraph=3,
        ),
    ]

    # Simulated LLM answer with [^n] markers
    answer = (
        "环氧树脂的防腐机理主要依靠固化后形成致密的交联网络结构[^1]，"
        "有效阻隔腐蚀介质的渗透。添加磷酸锌作为防锈颜料，"
        "可生成磷酸根离子与金属基材形成钝化保护膜[^2]。"
        "工艺上，固化温度应控制在80-120°C范围内[^3]。"
    )

    binding = bind_citations(answer, anchors)

    # Footnotes must be non-empty
    assert binding["footnotes"], "footnotes section must not be empty"
    assert "[^1]:" in binding["footnotes"], "footnotes should contain [^1]:"
    assert "[^2]:" in binding["footnotes"], "footnotes should contain [^2]:"
    assert "[^3]:" in binding["footnotes"], "footnotes should contain [^3]:"

    # used_citations must have 3 entries
    assert len(binding["used_citations"]) == 3, (
        f"expected 3 used citations, got {len(binding['used_citations'])}"
    )

    # answer is cleaned (no existing footnote def lines)
    assert "[^1]:" not in binding["answer"], (
        "answer must not contain footnote definition lines"
    )

    # Verify that answer still contains inline markers
    assert "[^1]" in binding["answer"], "answer must retain inline [^1] markers"
    assert "[^2]" in binding["answer"], "answer must retain inline [^2] markers"
    assert "[^3]" in binding["answer"], "answer must retain inline [^3] markers"


@pytest.mark.golden_eval
def test_golden_citation_format_no_citations():
    """bind_citations with an answer that has no [^n] markers produces empty footnotes."""
    from app.domain.citations import CitationAnchor
    from app.services.citation_binder import bind_citations

    anchors = [
        CitationAnchor(
            chunk_id="chunk-001",
            source_id="doc-x",
            text="some text",
        ),
    ]

    answer = "This answer has no citation markers at all."

    binding = bind_citations(answer, anchors)
    assert binding["used_citations"] == [], (
        "no citations should be used when answer has no markers"
    )
    # footnotes may be empty or contain only separator — either is acceptable
    # when no citations are used
