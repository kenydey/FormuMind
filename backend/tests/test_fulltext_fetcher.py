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

    monkeypatch.setattr(ff, "_resolve_oa_candidates", lambda ev, t: ([], []))
    with pytest.raises(ff.FetchError) as e1:
        ff._fetch_literature_text(ev, timeout=5)
    assert e1.value.reason == "无 OA 版本"

    monkeypatch.setattr(ff, "_resolve_oa_candidates", lambda ev, t: (["https://oa.example/x.pdf"], []))
    monkeypatch.setattr(
        "app.services.pdf_downloader.fetch_pdf_ex", lambda url, timeout=20: (None, "status:403")
    )
    with pytest.raises(ff.FetchError) as e2:
        ff._fetch_literature_text(ev, timeout=5)
    assert e2.value.reason == "OA 全文获取失败: status:403"

    monkeypatch.setattr(
        "app.services.pdf_downloader.fetch_pdf_ex", lambda url, timeout=20: (b"%PDF-fake", "ok")
    )
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: "")
    with pytest.raises(ff.FetchError) as e3:
        ff._fetch_literature_text(ev, timeout=5)
    assert e3.value.reason == "OA 全文获取失败: extract-empty"


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
    monkeypatch.setattr(ff, "_persist_fulltext", lambda text, ev, kind, **kw: persisted.append((ev.identifier, kind)) or "sid")

    before = _ev("US1234567")
    out, report = ff.enrich_search_results([before, _ev("plaintitle", source="notebooklm")])

    chunk_ids = [e.identifier for e in out if e.identifier.startswith("US1234567#p")]
    assert len(chunk_ids) >= 3  # full text became multiple chunks
    assert out[0].identifier == "US1234567#p0"  # replaced in position
    assert out[-1].identifier == "plaintitle"   # unfetchable row untouched
    assert report.succeeded == 1
    assert report.by_kind == {"patent": 1}
    assert persisted == [("US1234567", "patent")]


def test_failed_fetch_removes_row(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: None)
    rows = [_ev("US1234567")]
    out, report = ff.enrich_search_results(rows, persist=False)
    assert out == []  # 下载失败 → 移除，不进结果/左栏
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
    # `.example` TLD often resolves to reserved/blocked IPs under SSRF DNS checks.
    monkeypatch.setattr("app.services.ingestion._is_safe_url", lambda url: True)

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
    assert out == []  # unsafe URL 无法下载全文 → 移除
    assert report.succeeded == 0


def test_literature_oa_flow(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_resolve_oa_candidates", lambda ev, t: (["https://oa.example/x.pdf"], []))
    monkeypatch.setattr(
        "app.services.pdf_downloader.fetch_pdf_ex", lambda url, timeout=20: (b"%PDF-fake", "ok")
    )
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)
    out, report = ff.enrich_search_results(
        [_ev("10.1016/j.porgcoat.2020.105678", source="OpenAlex")], persist=False
    )
    assert report.by_kind == {"literature": 1}
    assert out[0].identifier.endswith("#p0")



def test_arxiv_identifier_resolves_pdf_url(monkeypatch):
    """Legacy arXiv ids still resolve to pdf.arxiv.org via URL pattern (no package)."""
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_resolve_oa_candidates", lambda ev, t: (["https://arxiv.org/pdf/2401.12345"], []))
    monkeypatch.setattr(
        "app.services.pdf_downloader.fetch_pdf_ex", lambda url, timeout=20: (b"%PDF-fake", "ok")
    )
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)

    out, report = ff.enrich_search_results([_ev("arXiv:2401.12345", source="OpenAlex")], persist=False)
    assert report.by_kind == {"literature": 1}


def test_doi_literature_fetch_uses_oa_pdf(monkeypatch):
    """A plain DOI uses OA PDF candidates only."""
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_resolve_oa_candidates", lambda ev, t: (["https://oa.example/x.pdf"], []))
    monkeypatch.setattr(
        "app.services.pdf_downloader.fetch_pdf_ex", lambda url, timeout=20: (b"%PDF-fake", "ok")
    )
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


def test_is_db_locked_walks_exception_chain():
    """database is locked 常被 SQLAlchemy 包装成 transaction-rolled-back。"""
    from sqlalchemy.exc import InvalidRequestError, OperationalError

    lock = OperationalError("stmt", {}, Exception("database is locked"))
    wrapped = InvalidRequestError("This Session's transaction has been rolled back")
    wrapped.__cause__ = lock
    assert ff._is_db_locked(wrapped) is True
    assert ff._is_db_locked(RuntimeError("boom")) is False


def test_persist_fulltext_retries_on_db_locked(monkeypatch):
    """_persist_fulltext retries the whole persist on database is locked."""
    from sqlalchemy.exc import OperationalError

    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda s: None)

    calls = {"n": 0}

    class FakeStore:
        def find_by_hash(self, h):
            return None

        def create(self, **kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                lock = OperationalError("stmt", {}, Exception("database is locked"))
                wrapped = Exception(
                    "This Session's transaction has been rolled back due to a previous exception during flush"
                )
                wrapped.__cause__ = lock
                raise wrapped
            return "src-1"

    monkeypatch.setattr("app.db.source_store.get_source_store", lambda: FakeStore())
    monkeypatch.setattr("app.services.kb_index.index_source", lambda sid, text: 1)

    ev = Evidence(source="USPTO", identifier="US1", title="t", snippet="s", relevance=0.9)
    result = ff._persist_fulltext("some full text " * 50, ev, "patent")
    assert result == "src-1"
    assert calls["n"] == 3  # 2 次失败 + 1 次成功


def test_persist_forces_patent_kind_for_pub_id(monkeypatch):
    """When identifier/url normalizes to a patent pub, persist as source_kind=patent."""
    created = {}

    class FakeStore:
        def find_by_hash(self, h):
            return None

        def create(self, **kw):
            created.update(kw)
            return "sid-1"

    monkeypatch.setattr("app.db.source_store.get_source_store", lambda: FakeStore())
    monkeypatch.setattr("app.services.kb_index.index_source", lambda sid, text: 1)

    ev = Evidence(
        source="internet",
        identifier="CN104789083B",
        title="demo",
        snippet="x",
        relevance=0.9,
        url="https://patents.google.com/patent/CN104789083B",
    )
    ff._persist_fulltext("full text body " * 40, ev, "web")
    assert created["source_kind"] == "patent"


# ── OpenAlex content archive (2026-09-11) ────────────────────────────────────
# The tier exists because the publisher-facing OA chain is the weakest link: a
# pdf_url on a Cloudflare-fronted host answers 403 and the document is lost even
# though copies exist. These tests pin the three properties that make it safe to
# put *ahead* of the existing chain — no key means no network, TEI XML is
# preferred over the PDF, and a miss falls through instead of failing the fetch.

_TEI_SAMPLE = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div>'
    b"<head>Introduction</head>"
    b"<p>Magnesium alloy passivation with cerium nitrate.</p>"
    b"<p>Neutral salt spray reached 720 hours.</p>"
    # Long enough to clear the shared "is this actually a document" floor that
    # the PDF tier also applies; a real GROBID file is orders of magnitude past it.
    + b"<p>Zirconium conversion coating on AZ91D substrate improves corrosion "
    b"resistance and the adhesion of the subsequent organic coating layer.</p>" * 4
    + b"</div></body></text></TEI>"
)


class _FakeResp:
    def __init__(self, status_code: int = 200, content: bytes = b"", payload: dict | None = None):
        self.status_code = status_code
        self.content = content
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Answer by URL fragment so a test can express a route table."""

    routes: dict = {}
    seen: list = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        type(self).seen.append(url)
        for frag, resp in type(self).routes.items():
            if frag in url:
                return resp
        return _FakeResp(404)


def _with_key(monkeypatch):
    monkeypatch.setenv("FORMUMIND_OPENALEX_API_KEY", "test-key")
    get_settings.cache_clear()


def test_tei_to_text_keeps_headings_and_paragraphs():
    text = ff._tei_to_text(_TEI_SAMPLE)
    assert "## Introduction" in text
    assert "cerium nitrate" in text
    assert "720 hours" in text


def test_tei_to_text_returns_empty_for_junk():
    """Empty (not garbage) so the caller falls through to the PDF tier."""
    assert ff._tei_to_text(b"not xml at all <<<") == ""


def test_maybe_gunzip_unwraps_gzip_body():
    """The live endpoint serves TEI XML as gzip (Content-Type: application/gzip).

    httpx only decodes a `Content-Encoding: gzip` header, not a gzip *body*, so
    without this the XML parser gets binary and yields nothing. Raw payloads must
    pass through untouched.
    """
    import gzip as _gz

    assert ff._maybe_gunzip(_gz.compress(_TEI_SAMPLE)) == _TEI_SAMPLE
    assert ff._maybe_gunzip(b"%PDF-1.7 raw") == b"%PDF-1.7 raw"
    assert ff._maybe_gunzip(b"") == b""


def test_openalex_content_handles_gzipped_tei(monkeypatch):
    """End-to-end through the tier: a gzipped TEI body still yields text."""
    import gzip as _gz

    _with_key(monkeypatch)
    _FakeClient.seen = []
    _FakeClient.routes = {
        "api.openalex.org": _FakeResp(200, payload={"id": "https://openalex.org/W123456789"}),
        ".grobid-xml": _FakeResp(200, content=_gz.compress(_TEI_SAMPLE)),
    }
    monkeypatch.setattr(ff.httpx, "Client", _FakeClient)

    text = ff._openalex_content_text(_ev("10.3390/coatings11040392", source="OpenAlex"), 5)
    assert text and "cerium nitrate" in text


def test_openalex_content_tier_needs_a_key(monkeypatch):
    """No key ⇒ total no-op: no HTTP client is even constructed, no spend."""
    monkeypatch.delenv("FORMUMIND_OPENALEX_API_KEY", raising=False)
    get_settings.cache_clear()
    _FakeClient.routes, _FakeClient.seen = {}, []
    monkeypatch.setattr(ff.httpx, "Client", _FakeClient)
    ev = _ev("10.3390/coatings11040392", source="OpenAlex")
    assert ff._openalex_content_text(ev, 5) is None
    assert _FakeClient.seen == []


def test_openalex_content_tier_skips_arxiv(monkeypatch):
    """arXiv serves its own PDFs for free — paying per file would be waste."""
    _with_key(monkeypatch)
    _FakeClient.routes, _FakeClient.seen = {}, []
    monkeypatch.setattr(ff.httpx, "Client", _FakeClient)
    assert ff._openalex_content_text(_ev("arXiv:2401.12345", source="arxiv"), 5) is None
    assert _FakeClient.seen == []


def test_openalex_content_prefers_tei_over_pdf(monkeypatch):
    """TEI XML first: it arrives as structured text, skipping parse/OCR entirely."""
    _with_key(monkeypatch)
    _FakeClient.seen = []
    _FakeClient.routes = {
        "api.openalex.org": _FakeResp(200, payload={"id": "https://openalex.org/W123456789"}),
        ".grobid-xml": _FakeResp(200, content=_TEI_SAMPLE),
        ".pdf": _FakeResp(200, content=b"%PDF-fake"),
    }
    monkeypatch.setattr(ff.httpx, "Client", _FakeClient)

    text = ff._openalex_content_text(_ev("10.3390/coatings11040392", source="OpenAlex"), 5)
    assert text and "cerium nitrate" in text
    assert any(".grobid-xml" in u for u in _FakeClient.seen)
    assert not any(u.endswith(".pdf") for u in _FakeClient.seen), "PDF must not be fetched when TEI works"


def test_openalex_content_falls_back_to_pdf_when_tei_missing(monkeypatch):
    _with_key(monkeypatch)
    _FakeClient.seen = []
    _FakeClient.routes = {
        "api.openalex.org": _FakeResp(200, payload={"id": "https://openalex.org/W123456789"}),
        ".grobid-xml": _FakeResp(404),
        ".pdf": _FakeResp(200, content=b"%PDF-fake"),
    }
    monkeypatch.setattr(ff.httpx, "Client", _FakeClient)
    monkeypatch.setattr("app.services.pdf_downloader._extract_text", lambda content: LONG_TEXT)

    text = ff._openalex_content_text(_ev("10.3390/coatings11040392", source="OpenAlex"), 5)
    assert text == LONG_TEXT
    assert any(u.endswith(".pdf") for u in _FakeClient.seen)


def test_openalex_content_miss_returns_none(monkeypatch):
    """A work with no cached content must degrade, not raise."""
    _with_key(monkeypatch)
    _FakeClient.seen = []
    _FakeClient.routes = {
        "api.openalex.org": _FakeResp(200, payload={"id": "https://openalex.org/W123456789"}),
        ".grobid-xml": _FakeResp(404),
        ".pdf": _FakeResp(404),
    }
    monkeypatch.setattr(ff.httpx, "Client", _FakeClient)
    assert ff._openalex_content_text(_ev("10.3390/coatings11040392", source="OpenAlex"), 5) is None


def test_literature_fetch_prefers_openalex_content(monkeypatch):
    """A content-archive hit must short-circuit the publisher chain entirely —
    that is the whole point: those URLs are what answer 403."""
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_openalex_content_text", lambda ev, t, **kw: LONG_TEXT)
    publisher_hits: list = []

    def _no_oa(ev, t):
        publisher_hits.append(ev.identifier)
        return [], []

    monkeypatch.setattr(ff, "_resolve_oa_candidates", _no_oa)

    out, report = ff.enrich_search_results(
        [_ev("10.3390/coatings11040392", source="OpenAlex")], persist=False
    )
    assert report.by_kind == {"literature": 1}
    assert publisher_hits == [], "publisher resolution must not run after a content hit"


def test_literature_fetch_falls_through_when_content_misses(monkeypatch):
    """Content-archive miss ⇒ unchanged legacy behaviour (here: no OA version)."""
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_openalex_content_text", lambda ev, t, **kw: None)
    monkeypatch.setattr(ff, "_resolve_oa_candidates", lambda ev, t: ([], []))

    with pytest.raises(ff.FetchError) as e:
        ff._fetch_literature_text(_ev("10.3390/coatings11040392", source="OpenAlex"), timeout=5)
    assert e.value.reason == "无 OA 版本"
