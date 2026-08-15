"""KB P0 tests — full-text acquisition layer (patent / OA literature / web)."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import fulltext_fetcher as ff


LONG_TEXT = "\n\n".join(
    f"Section {i}. Epoxy-amine coating full text paragraph with formulation details, "
    "zinc phosphate loadings, cure schedules and salt spray results measured on steel."
    for i in range(40)
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _enable(monkeypatch, max_docs: int = 8):
    monkeypatch.setenv("FORMUMIND_FULLTEXT_ENRICH", "true")
    monkeypatch.setenv("FORMUMIND_FULLTEXT_MAX_DOCS", str(max_docs))
    get_settings.cache_clear()


def _ev(identifier: str, source: str = "USPTO", relevance: float = 0.9) -> Evidence:
    return Evidence(
        source=source, identifier=identifier, title=f"Doc {identifier}",
        snippet="abstract only", relevance=relevance,
    )


# ── classification ───────────────────────────────────────────────────────────


def test_classify_kinds():
    assert ff.classify(_ev("US1234567")) == "patent"
    assert ff.classify(_ev("EP2345678A1")) == "patent"
    assert ff.classify(_ev("10.1016/j.porgcoat.2020.105678", source="OpenAlex")) == "literature"
    assert ff.classify(_ev("doi:10.1000/xyz", source="literature")) == "literature"
    assert ff.classify(_ev("arXiv:2401.12345", source="arxiv")) == "literature"
    assert ff.classify(_ev("https://arxiv.org/abs/2401.12345", source="arxiv")) == "literature"
    assert ff.classify(_ev("https://tech.example/article", source="internet")) == "web"


def test_classify_skips_chunks_seeds_and_unknown():
    assert ff.classify(_ev("US1234567#p3")) is None
    assert ff.classify(_ev("local-file#2", source="local")) is None
    seed = Evidence(source="seed", identifier="US999", title="s", snippet="x",
                    relevance=0.5, is_seed_corpus=True)
    assert ff.classify(seed) is None
    assert ff.classify(_ev("just a title", source="notebooklm")) is None


def test_arxiv_pdf_url_resolution_needs_no_network():
    url = ff._resolve_oa_pdf_url(_ev("arXiv:2401.12345", source="arxiv"), timeout=5)
    assert url == "https://arxiv.org/pdf/2401.12345"


def test_literature_fetch_error_reasons(monkeypatch):
    """Fetchers must report *why* literature text was unobtainable — the three
    failure modes (无 OA / 下载超时 / 解析为空) used to be collapsed into one
    opaque message, hiding that most MDPI (fully-OA) failures were timeouts or
    empty parses rather than genuinely paywalled papers."""
    _enable(monkeypatch)
    ev = _ev("10.3390/coatings14010123", source="OpenAlex")

    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: None)
    with pytest.raises(ff.FetchError) as e1:
        ff._fetch_literature_text(ev, timeout=5)
    assert e1.value.reason == "无 OA 版本"

    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: "https://oa.example/x.pdf")
    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: None)
    with pytest.raises(ff.FetchError) as e2:
        ff._fetch_literature_text(ev, timeout=5)
    assert e2.value.reason == "下载超时"

    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: b"%PDF-fake")
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: "")
    with pytest.raises(ff.FetchError) as e3:
        ff._fetch_literature_text(ev, timeout=5)
    assert e3.value.reason == "解析为空"


# ── enrichment flow ──────────────────────────────────────────────────────────


def test_disabled_flag_is_strict_noop(monkeypatch):
    called = []
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: called.append(ev) or LONG_TEXT)
    rows = [_ev("US1234567")]
    out, report = ff.enrich_search_results(rows)
    assert out == rows
    assert called == []
    assert report.attempted == 0


def test_patent_hit_replaced_by_fulltext_chunks(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    persisted = []
    monkeypatch.setattr(ff, "_persist_fulltext", lambda text, ev, kind: persisted.append((ev.identifier, kind)) or "sid")

    before = _ev("US1234567")
    out, report = ff.enrich_search_results([before, _ev("plaintitle", source="notebooklm")])

    chunk_ids = [e.identifier for e in out if e.identifier.startswith("US1234567#p")]
    assert len(chunk_ids) >= 3  # full text became multiple chunks
    assert out[0].identifier == "US1234567#p0"  # replaced in position
    assert out[-1].identifier == "plaintitle"   # unfetchable row untouched
    assert report.succeeded == 1
    assert report.by_kind == {"patent": 1}
    assert persisted == [("US1234567", "patent")]


def test_failed_fetch_keeps_original(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: None)
    rows = [_ev("US1234567")]
    out, report = ff.enrich_search_results(rows, persist=False)
    assert out == rows
    assert report.attempted == 1
    assert report.succeeded == 0


def test_max_docs_cap(monkeypatch):
    _enable(monkeypatch, max_docs=1)
    calls = []

    def fake_fetch(ev, t):
        calls.append(ev.identifier)
        return LONG_TEXT

    monkeypatch.setattr(ff, "_fetch_patent_text", fake_fetch)
    rows = [_ev("US1110001"), _ev("US2220002"), _ev("US3330003")]
    out, report = ff.enrich_search_results(rows, persist=False)
    assert calls == ["US1110001"]  # only the top-ranked row attempted
    assert any(e.identifier == "US2220002" for e in out)  # others pass through
    assert report.attempted == 1


def test_web_fetch_uses_trafilatura_fallback_chain(monkeypatch):
    _enable(monkeypatch)

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<html><body>" + "".join(
            f"<p>Paragraph {i}: waterborne polyurethane dispersion coating full text.</p>"
            for i in range(30)
        ) + "</body></html>"
        content = b""

    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(ff.httpx, "Client", FakeClient)
    out, report = ff.enrich_search_results(
        [_ev("https://tech.example/article", source="internet")], persist=False
    )
    assert report.succeeded == 1
    assert report.by_kind == {"web": 1}
    assert out[0].identifier.endswith("#p0")
    assert "polyurethane" in out[0].snippet


def test_web_fetch_refuses_unsafe_urls(monkeypatch):
    _enable(monkeypatch)
    out, report = ff.enrich_search_results(
        [_ev("http://127.0.0.1/internal", source="internet")], persist=False
    )
    assert out[0].identifier == "http://127.0.0.1/internal"  # untouched
    assert report.succeeded == 0


def test_literature_oa_flow(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: "https://oa.example/x.pdf")
    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: b"%PDF-fake")
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)
    out, report = ff.enrich_search_results(
        [_ev("10.1016/j.porgcoat.2020.105678", source="OpenAlex")], persist=False
    )
    assert report.by_kind == {"literature": 1}
    assert out[0].identifier.endswith("#p0")


def test_arxiv_prefers_latex_source_over_pdf(monkeypatch):
    """arXiv source first: the PDF path is where the time goes.

    Measured on a 100-page paper: ~53 s via PDF (of which ~50 s was RapidOCR
    firing on figure-heavy pages) against ~1.2 s via the source.
    """
    _enable(monkeypatch)
    pdf_calls: list[str] = []
    monkeypatch.setattr(
        "app.services.arxiv_source.fetch_arxiv_markdown",
        lambda aid, timeout=20: "## Introduction\n\n" + LONG_TEXT,
    )
    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: pdf_calls.append("resolved") or None)

    out, report = ff.enrich_search_results([_ev("arXiv:2401.12345", source="arxiv")], persist=False)
    assert report.by_kind == {"literature": 1}
    assert pdf_calls == [], "the PDF path must not be touched when source succeeds"


def test_arxiv_falls_back_to_pdf_when_no_source(monkeypatch):
    """PDF-only submissions exist; they must keep working exactly as before."""
    _enable(monkeypatch)
    monkeypatch.setattr("app.services.arxiv_source.fetch_arxiv_markdown", lambda aid, timeout=20: None)
    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: "https://arxiv.org/pdf/2401.12345")
    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: b"%PDF-fake")
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)

    out, report = ff.enrich_search_results([_ev("arXiv:2401.12345", source="arxiv")], persist=False)
    assert report.by_kind == {"literature": 1}


def test_arxiv_source_crash_does_not_lose_the_document(monkeypatch):
    """A raising source fetcher must degrade to the PDF, not fail the document."""
    _enable(monkeypatch)

    def boom(aid, timeout=20):
        raise RuntimeError("tarfile exploded")

    monkeypatch.setattr("app.services.arxiv_source.fetch_arxiv_markdown", boom)
    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: "https://arxiv.org/pdf/2401.12345")
    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: b"%PDF-fake")
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)

    out, report = ff.enrich_search_results([_ev("arXiv:2401.12345", source="arxiv")], persist=False)
    assert report.by_kind == {"literature": 1}


def test_arxiv_source_can_be_disabled(monkeypatch):
    """`arxiv_prefer_source=False` restores the pre-change behaviour exactly."""
    _enable(monkeypatch)
    monkeypatch.setenv("FORMUMIND_ARXIV_PREFER_SOURCE", "false")
    get_settings.cache_clear()

    def unexpected(aid, timeout=20):
        raise AssertionError("source path must not run when disabled")

    monkeypatch.setattr("app.services.arxiv_source.fetch_arxiv_markdown", unexpected)
    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: "https://arxiv.org/pdf/2401.12345")
    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: b"%PDF-fake")
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)

    out, report = ff.enrich_search_results([_ev("arXiv:2401.12345", source="arxiv")], persist=False)
    assert report.by_kind == {"literature": 1}
    get_settings.cache_clear()


def test_non_arxiv_doi_never_touches_the_source_path(monkeypatch):
    """A plain DOI has no arXiv id, so the source fetcher must not be consulted."""
    _enable(monkeypatch)

    def unexpected(aid, timeout=20):
        raise AssertionError("source path must not run for a bare DOI")

    monkeypatch.setattr("app.services.arxiv_source.fetch_arxiv_markdown", unexpected)
    monkeypatch.setattr(ff, "_resolve_oa_pdf_url", lambda ev, t: "https://oa.example/x.pdf")
    monkeypatch.setattr("app.services.pdf_downloader.fetch_pdf", lambda url, timeout=20: b"%PDF-fake")
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)

    out, report = ff.enrich_search_results(
        [_ev("10.1016/j.porgcoat.2020.105678", source="OpenAlex")], persist=False
    )
    assert report.by_kind == {"literature": 1}


def test_chunks_carry_provenance_and_relevance_decay(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    out, _ = ff.enrich_search_results([_ev("US7770007", relevance=0.9)], persist=False)
    assert out[0].source == "USPTO"
    assert out[0].relevance == pytest.approx(0.9)
    assert out[1].relevance < out[0].relevance
    # Snippets used to be clipped to a hardcoded 600 characters, which discarded
    # most of a chunk that had just been downloaded and parsed. The bound that
    # actually means something is the configured chunk size — the fetched text
    # reaches the model whole, not at a third of its length.
    limit = get_settings().ingest_chunk_max_chars
    assert all(len(e.snippet) <= limit for e in out)
    assert any(len(e.snippet) > 600 for e in out), "full chunks must survive"
