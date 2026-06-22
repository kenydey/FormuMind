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


# ── URL construction ──────────────────────────────────────────────────────────


def test_patent_pdf_url_uspto():
    url = pd._patent_pdf_url("US9982145B2")
    assert url is not None
    assert "pdfpiw.uspto.gov" in url
    assert "09982145" in url


def test_patent_pdf_url_epo():
    url = pd._patent_pdf_url("EP3211048A1")
    assert url is not None
    assert "epo.org" in url
    assert "3211048" in url


def test_patent_pdf_url_unknown_returns_none():
    assert pd._patent_pdf_url("DOI:10.1016/j.foo.2020") is None
    assert pd._patent_pdf_url("") is None


def test_google_patents_url_format():
    url = pd._google_patents_url("US9982145B2")
    assert "patents.google.com" in url
    assert "US9982145B2" in url


# ── fetch_pdf — network stubbed to None ─────────────────────────────────────


def test_fetch_pdf_returns_none_on_error(monkeypatch):
    import httpx

    def _bad_get(*a, **kw):
        raise httpx.ConnectError("stubbed")

    monkeypatch.setattr(httpx.Client, "get", _bad_get)
    result = pd.fetch_pdf("https://example.com/fake.pdf", timeout=1)
    assert result is None


def test_fetch_patent_pdf_returns_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(pd, "fetch_pdf", lambda *a, **kw: None)
    result = pd.fetch_patent_pdf("US9982145B2")
    assert result is None


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
