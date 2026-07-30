"""Tests for the patent PDF downloader (v0.9).

All assertions run fully offline: network calls are monkeypatched to return
None (simulating download failure) or fake bytes with known text content.
The extraction chain (markitdown → pypdf) is also stubbed so tests don't
require those optional libraries.
"""
from __future__ import annotations

import pytest

from app.domain.schemas import Evidence
from app.services import pdf_downloader as pd

# Shaped like a real Google Patents landing page: the `citation_pdf_url` meta
# tag plus the three `itemprop` sections that carry the full text.
_LANDING_HTML = """<html><head>
<meta name="citation_patent_number" content="CN102345678A"/>
<meta name="citation_pdf_url" content="https://patentimages.storage.googleapis.com/1d/a5/CN102345678A.pdf"/>
</head><body>
<section itemprop="abstract"><h2>Abstract</h2><div>A waterborne epoxy primer
containing zinc phosphate for corrosion protection of carbon steel substrates
in marine atmospheric service conditions.</div></section>
<section itemprop="description"><h2>Description</h2>
<div>Translated from Chinese</div>
<div>The present invention relates to a two-component waterborne coating in
which the curing agent is an amine adduct. Pigment volume concentration is
held between 30 and 40 percent to balance barrier properties.</div>
</section>
<section itemprop="claims"><h2>Claims (12)</h2>
<div>1. A waterborne epoxy coating composition comprising zinc phosphate in an
amount of 5 to 15 weight percent based on total solids.</div>
</section>
<section itemprop="similarDocuments"><div>US1234567A</div></section>
</body></html>"""


# ── URL construction ──────────────────────────────────────────────────────────


def test_landing_url_uses_english_rendering():
    url = pd._landing_url("us9982145b2")
    assert url == "https://patents.google.com/patent/US9982145B2/en"


def test_dead_endpoints_are_never_requested(monkeypatch):
    """The three direct-PDF URLs this module used to guess are all dead.

    Measured against the live endpoints: pdfpiw resets the connection,
    ``/patent/{id}/pdf`` answers 200 with ``text/html``, and the EPO
    publication server answers 200 with a 2.4 KB error page even for a valid
    publication number. Each cost a full timeout per patent and returned
    nothing, so this asserts on the URLs actually requested — a US and an EP
    number are the two that used to take the office-specific branches.
    """
    requested: list[str] = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = _LANDING_HTML

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *a, **kw):
            requested.append(str(url))
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    pd.fetch_patent_pdf("US9982145B2")
    pd.fetch_patent_pdf("EP3211048A1")

    assert requested, "expected at least the landing-page requests"
    joined = " ".join(requested)
    assert "pdfpiw" not in joined
    assert "data.epo.org" not in joined
    # The `/pdf` suffix on patents.google.com serves HTML, not a PDF.
    assert not any(u.startswith("https://patents.google.com") and u.endswith("/pdf") for u in requested)
    assert requested[0] == "https://patents.google.com/patent/US9982145B2/en"


# ── fetch_pdf — network stubbed to None ─────────────────────────────────────


def test_fetch_pdf_returns_none_on_error(monkeypatch):
    import httpx

    def _bad_get(*a, **kw):
        raise httpx.ConnectError("stubbed")

    monkeypatch.setattr(httpx.Client, "get", _bad_get)
    result = pd.fetch_pdf("https://example.com/fake.pdf", timeout=1)
    assert result is None


def test_fetch_patent_pdf_returns_none_when_landing_fails(monkeypatch):
    monkeypatch.setattr(pd, "fetch_patent_landing", lambda *a, **kw: None)
    assert pd.fetch_patent_pdf("US9982145B2") is None


def test_fetch_patent_pdf_does_not_download_when_page_has_no_pdf(monkeypatch):
    """No ``citation_pdf_url`` must mean no download attempt, not a guess.

    JP publications (and some EP ones) genuinely have no PDF on Google Patents.
    Falling back to a constructed URL is what burned the timeouts.
    """
    calls: list[str] = []
    monkeypatch.setattr(pd, "fetch_patent_landing", lambda *a, **kw: "<html>no meta here</html>")
    monkeypatch.setattr(pd, "fetch_pdf", lambda url, *a, **kw: calls.append(url) or None)
    assert pd.fetch_patent_pdf("JPH0925455A") is None
    assert calls == []


# ── landing page → text ──────────────────────────────────────────────────────


def test_patent_text_from_html_keeps_all_three_sections_in_order():
    md = pd.patent_text_from_html(_LANDING_HTML)
    assert md.index("## Abstract") < md.index("## Description") < md.index("## Claims")
    assert "zinc phosphate" in md
    assert "amine adduct" in md  # description body
    assert "5 to 15 weight percent" in md  # claims body
    # Unrelated sections must not leak in.
    assert "US1234567A" not in md
    # The section's own <h2> must not be duplicated under our heading.
    assert md.count("Abstract") == 1


def test_patent_text_from_html_empty_when_no_sections():
    assert pd.patent_text_from_html("<html><body>Not found</body></html>") == ""


def test_patent_text_from_html_decodes_entities():
    html = (
        '<section itemprop="abstract"><div>'
        "Cure at 25&#176;C with &lt;5% VOC &amp; high gloss retention over "
        "extended weathering exposure trials.</div></section>"
    )
    md = pd.patent_text_from_html(html)
    assert "25°C" in md
    assert "<5% VOC & high gloss" in md


# ── citation_pdf_url extraction is host-pinned ───────────────────────────────


def test_pdf_url_from_landing_accepts_the_google_bucket():
    url = pd._pdf_url_from_landing(_LANDING_HTML)
    assert url == "https://patentimages.storage.googleapis.com/1d/a5/CN102345678A.pdf"


def test_pdf_url_from_landing_handles_attribute_order():
    html = '<meta content="https://patentimages.storage.googleapis.com/x/y.pdf" name="citation_pdf_url"/>'
    assert pd._pdf_url_from_landing(html) == "https://patentimages.storage.googleapis.com/x/y.pdf"


@pytest.mark.parametrize(
    "bad",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://localhost:8000/admin",
        "https://evil.example.com/patent.pdf",
        "file:///etc/passwd",
    ],
)
def test_pdf_url_from_landing_rejects_foreign_hosts(bad):
    """The meta tag comes off a third-party page, so it is untrusted input.

    Without the host pin, a tampered or man-in-the-middled landing page would
    turn every patent fetch into a request of the attacker's choosing.
    """
    html = f'<meta name="citation_pdf_url" content="{bad}"/>'
    assert pd._pdf_url_from_landing(html) is None


def test_pdf_url_from_landing_none_when_absent():
    assert pd._pdf_url_from_landing("<html><head></head></html>") is None


# ── fetch_patent_text: HTML preferred, PDF on request ────────────────────────


def test_fetch_patent_text_prefers_html_and_makes_one_request(monkeypatch):
    """HTML-first must not download the PDF at all — that is the speed win."""
    downloads: list[str] = []
    monkeypatch.setattr(pd, "fetch_patent_landing", lambda *a, **kw: _LANDING_HTML)
    monkeypatch.setattr(pd, "fetch_pdf", lambda url, *a, **kw: downloads.append(url) or b"X")

    text = pd.fetch_patent_text("CN102345678A", prefer_html=True)
    assert text and "## Description" in text
    assert downloads == []


def test_fetch_patent_text_uses_pdf_when_html_not_preferred(monkeypatch):
    monkeypatch.setattr(pd, "fetch_patent_landing", lambda *a, **kw: _LANDING_HTML)
    monkeypatch.setattr(pd, "fetch_pdf", lambda *a, **kw: b"%PDF-1.4 fake")
    monkeypatch.setattr(pd, "_extract_text", lambda _: "PARSED PDF BODY " * 30)

    text = pd.fetch_patent_text("CN102345678A", prefer_html=False)
    assert text is not None
    assert text.startswith("PARSED PDF BODY")


def test_fetch_patent_text_falls_back_to_html_when_pdf_unusable(monkeypatch):
    """A dead PDF link is no reason to discard a page that holds the full text.

    This is the case that used to produce `failed`: the PDF tier came up empty
    and nothing looked at the description sitting in the same response.
    """
    monkeypatch.setattr(pd, "fetch_patent_landing", lambda *a, **kw: _LANDING_HTML)
    monkeypatch.setattr(pd, "fetch_pdf", lambda *a, **kw: None)

    text = pd.fetch_patent_text("CN102345678A", prefer_html=False)
    assert text is not None
    assert "amine adduct" in text


def test_fetch_patent_text_none_when_landing_unreachable(monkeypatch):
    monkeypatch.setattr(pd, "fetch_patent_landing", lambda *a, **kw: None)
    assert pd.fetch_patent_text("CN102345678A") is None


def test_fetch_patent_landing_returns_none_on_non_200(monkeypatch):
    class _Resp:
        status_code = 404
        text = "nope"

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _Client)
    assert pd.fetch_patent_landing("US9982145B2") is None


# ── pdf_to_evidence ───────────────────────────────────────────────────────────


def test_pdf_to_evidence_empty_when_no_text(monkeypatch):
    monkeypatch.setattr(pd, "_extract_text", lambda _: "")
    result = pd.pdf_to_evidence(b"fake", "USPTO", "US123", "Test", max_chunks=3)
    assert result == []


def test_pdf_to_evidence_chunks_text(monkeypatch):
    long_para = "Zinc phosphate epoxy primer with waterborne resin system for corrosion protection on carbon steel substrates."
    text = "\n\n".join([f"{long_para} Variant {i}." for i in range(5)])
    monkeypatch.setattr(pd, "_extract_text", lambda _: text)

    result = pd.pdf_to_evidence(
        b"fake", "USPTO", "US9982145B2", "Zinc Phosphate Primer", max_chunks=3
    )
    assert len(result) == 3
    assert result[0].source == "USPTO"
    assert result[0].identifier == "US9982145B2#p0"
    assert result[0].title == "Zinc Phosphate Primer"
    assert result[1].identifier == "US9982145B2#p1"
    # Relevance decreases across chunks.
    assert result[0].relevance > result[1].relevance > result[2].relevance


def test_pdf_to_evidence_skips_short_paragraphs(monkeypatch):
    # Short paragraph (< min_chunk_len=100) must be skipped.
    short = "Short."
    long_para = "A" * 120  # >= 100 chars
    text = f"{short}\n\n{long_para}\n\n{short}"
    monkeypatch.setattr(pd, "_extract_text", lambda _: text)

    result = pd.pdf_to_evidence(b"fake", "USPTO", "US1", "T", max_chunks=5)
    assert len(result) == 1
    assert result[0].snippet.startswith("A")


# ── enrich_with_fulltext ──────────────────────────────────────────────────────


def _ev(identifier: str, source: str = "USPTO") -> Evidence:
    return Evidence(
        source=source, identifier=identifier,
        title=f"Patent {identifier}", snippet="Abstract.", relevance=0.9,
    )


def test_enrich_passthrough_when_download_fails(monkeypatch):
    monkeypatch.setattr(pd, "fetch_patent_pdf", lambda *a, **kw: None)
    ev = [_ev("US9982145B2"), _ev("EP3211048A1")]
    result = pd.enrich_with_fulltext(ev)
    assert result == ev


def test_enrich_replaces_with_chunks(monkeypatch):
    long_para = "Waterborne epoxy with zinc phosphate corrosion inhibitor provides excellent adhesion on carbon steel substrate."
    fake_text = "\n\n".join([f"{long_para} Para {i}." for i in range(4)])

    monkeypatch.setattr(pd, "fetch_patent_pdf", lambda *a, **kw: b"FAKE_PDF")
    monkeypatch.setattr(pd, "_extract_text", lambda _: fake_text)

    ev = [_ev("US9982145B2")]
    result = pd.enrich_with_fulltext(ev, max_pdfs=1)

    # Original single Evidence should be replaced by multiple chunks.
    assert len(result) > 1
    assert all(r.identifier.startswith("US9982145B2#p") for r in result)
    assert all(r.source == "USPTO" for r in result)


def test_enrich_respects_max_pdfs(monkeypatch):
    long_para = "Sufficient content paragraph about waterborne epoxy zinc phosphate corrosion-inhibiting coating formulation."
    fake_text = "\n\n".join([f"{long_para} Row {i}." for i in range(3)])

    monkeypatch.setattr(pd, "fetch_patent_pdf", lambda *a, **kw: b"FAKE")
    monkeypatch.setattr(pd, "_extract_text", lambda _: fake_text)

    ev = [_ev("US1111111"), _ev("US2222222"), _ev("US3333333")]
    result = pd.enrich_with_fulltext(ev, max_pdfs=1)

    # Only the first patent should be expanded; the rest stay as-is.
    chunk_ids = [r.identifier for r in result if "#p" in r.identifier]
    plain_ids = [r.identifier for r in result if "#p" not in r.identifier]
    assert all(cid.startswith("US1111111") for cid in chunk_ids)
    assert set(plain_ids) == {"US2222222", "US3333333"}


def test_enrich_skips_non_patent_identifiers(monkeypatch):
    monkeypatch.setattr(pd, "fetch_patent_pdf", lambda *a, **kw: b"FAKE")
    # DOI identifiers should be skipped (not patent numbers)
    ev = [_ev("DOI:10.1016/j.porgcoat.2019.105338", source="literature")]
    result = pd.enrich_with_fulltext(ev, max_pdfs=3)
    assert result == ev  # unchanged — not a patent identifier


# ── KnowledgeCohort PDF gate ──────────────────────────────────────────────────


def test_cohort_pdf_download_gate_is_off_by_default():
    """By default pdf_download=False, so enrich_with_fulltext is never called."""
    from app.config import get_settings
    settings = get_settings()
    assert settings.pdf_download is False, (
        "pdf_download must default to False to keep tests offline"
    )


# ── Real PDF fixture tests (no mocks — exercises actual parsing cascade) ───────

_FIXTURE = (
    __file__.replace("test_pdf_downloader.py", "fixtures/sample_patent.pdf")
)
_KNOWN_TOKENS = ["zinc phosphate", "500 hours", "waterborne epoxy"]


def _fixture_bytes() -> bytes:
    with open(_FIXTURE, "rb") as f:
        return f.read()


def test_extract_text_real_pdf():
    """_extract_text() on real fixture bytes returns non-empty text with known tokens.

    Does NOT monkeypatch _extract_text — this exercises the actual cascade
    (markitdown → pypdf → _raw_pdf_stream_text) on committed fixture bytes.
    """
    content = _fixture_bytes()
    text = pd._extract_text(content)
    assert text, "_extract_text returned empty string on real fixture"
    lower = text.lower()
    for tok in _KNOWN_TOKENS:
        assert tok in lower, f"Expected token '{tok}' not found in extracted text"


def test_pdf_to_evidence_real_pdf():
    """pdf_to_evidence() on real fixture bytes produces correct Evidence items.

    Validates identifier format, source/title pass-through, snippet content,
    and relevance decreasing across chunks.
    """
    content = _fixture_bytes()
    result = pd.pdf_to_evidence(
        content,
        source="USPTO",
        identifier="US9982145B2",
        title="Waterborne Epoxy Anticorrosive Coating",
        base_relevance=1.0,
    )
    assert len(result) >= 1, "Expected at least one Evidence chunk from real PDF"
    # Identifier pattern: original#p<n>
    assert result[0].identifier == "US9982145B2#p0"
    assert result[0].source == "USPTO"
    assert result[0].title == "Waterborne Epoxy Anticorrosive Coating"
    # At least one known token should appear somewhere across the chunks
    all_snippets = " ".join(c.snippet.lower() for c in result)
    assert any(tok in all_snippets for tok in _KNOWN_TOKENS), (
        "None of the known tokens found in pdf_to_evidence chunks"
    )
    # Relevance should decrease (or stay equal) across chunks
    if len(result) > 1:
        assert result[0].relevance >= result[1].relevance


def test_enrich_with_fulltext_real_pdf(monkeypatch):
    """enrich_with_fulltext() replaces a patent Evidence with real full-text chunks.

    fetch_patent_pdf is monkeypatched to return fixture bytes — zero network calls.
    Validates that the original abstract-only item is replaced with paragraph chunks.
    """
    content = _fixture_bytes()
    monkeypatch.setattr(pd, "fetch_patent_pdf", lambda *a, **kw: content)

    original = Evidence(
        source="USPTO",
        identifier="US9982145B2",
        title="Waterborne epoxy anticorrosive coating with zinc phosphate",
        snippet="Abstract only — short placeholder.",
        relevance=0.95,
    )
    result = pd.enrich_with_fulltext([original], max_pdfs=1)

    # Original single item should be replaced by full-text chunks
    assert len(result) >= 1
    chunk_ids = [e.identifier for e in result if "#p" in e.identifier]
    assert len(chunk_ids) >= 1, "No full-text chunks produced from real PDF fixture"
    assert all(cid.startswith("US9982145B2#p") for cid in chunk_ids)
    # Source and title should be inherited from the original Evidence
    assert all(e.source == "USPTO" for e in result)
    # At least one snippet should contain a known token
    all_text = " ".join(e.snippet.lower() for e in result)
    assert any(tok in all_text for tok in _KNOWN_TOKENS), (
        "None of the known tokens found in enriched Evidence snippets"
    )
