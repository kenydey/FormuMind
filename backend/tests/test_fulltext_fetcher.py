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
    rows = [_ev("US111"), _ev("US222"), _ev("US333")]
    out, report = ff.enrich_search_results(rows, persist=False)
    assert calls == ["US111"]  # only the top-ranked row attempted
    assert any(e.identifier == "US222" for e in out)  # others pass through
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


def test_chunks_carry_provenance_and_relevance_decay(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: LONG_TEXT)
    out, _ = ff.enrich_search_results([_ev("US777", relevance=0.9)], persist=False)
    assert out[0].source == "USPTO"
    assert out[0].relevance == pytest.approx(0.9)
    assert out[1].relevance < out[0].relevance
    assert all(len(e.snippet) <= 600 for e in out)
