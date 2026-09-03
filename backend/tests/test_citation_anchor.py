"""Task 2.3 tests: CitationAnchor — from_chunk_row, to_reference, to_citation_text."""
from __future__ import annotations

import pytest

from app.domain.citations import CitationAnchor


# ── Stub objects used to simulate ORM rows and schema responses ────────────────


class _StubORMChunk:
    """Simulates a SQLAlchemy ORM ``DocumentChunk`` row with ORM column names."""

    def __init__(
        self,
        id: str = "chunk-01",
        source_id: str = "src-42",
        text: str = "环氧树脂作为主要成膜物质具有优异的附着力和耐化学性",
        page_no: int | None = 3,
        paragraph_idx: int | None = 42,
        offset_start: int | None = 1024,
        offset_end: int | None = 1408,
    ):
        self.id = id
        self.source_id = source_id
        self.text = text
        self.page_no = page_no
        self.paragraph_idx = paragraph_idx
        self.offset_start = offset_start
        self.offset_end = offset_end


class _StubPydanticChunk:
    """Simulates a ``DocumentChunkResponse`` with Pydantic field names."""

    def __init__(
        self,
        id: str = "chunk-pydantic",
        source_id: str = "src-pydantic",
        text: str = "磷酸锌防锈颜料提升盐雾耐受",
        page: int | None = 7,
        paragraph: int | None = 5,
        offset_start: int | None = 2048,
        offset_end: int | None = 2560,
    ):
        self.id = id
        self.source_id = source_id
        self.text = text
        self.page = page
        self.paragraph = paragraph
        self.offset_start = offset_start
        self.offset_end = offset_end


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_from_chunk_row_orm():
    """Construct CitationAnchor from a stub ORM row — all fields map correctly."""
    row = _StubORMChunk()
    anchor = CitationAnchor.from_chunk_row(row)

    assert anchor.chunk_id == "chunk-01"
    assert anchor.source_id == "src-42"
    assert "环氧树脂" in anchor.text
    assert anchor.page == 3
    assert anchor.paragraph == 42
    assert anchor.offset_start == 1024
    assert anchor.offset_end == 1408


def test_from_chunk_row_pydantic():
    """Construct CitationAnchor from a stub Pydantic response — fields map."""
    chunk = _StubPydanticChunk()
    anchor = CitationAnchor.from_chunk_row(chunk)

    assert anchor.chunk_id == "chunk-pydantic"
    assert anchor.source_id == "src-pydantic"
    assert "磷酸锌" in anchor.text
    assert anchor.page == 7
    assert anchor.paragraph == 5
    assert anchor.offset_start == 2048
    assert anchor.offset_end == 2560


def test_from_chunk_row_nulls_default():
    """Null page / paragraph / offsets come through as None."""
    row = _StubORMChunk(page_no=None, paragraph_idx=None, offset_start=None, offset_end=None)
    anchor = CitationAnchor.from_chunk_row(row)

    assert anchor.page is None
    assert anchor.paragraph is None
    assert anchor.offset_start is None
    assert anchor.offset_end is None


def test_to_reference_format():
    """``to_reference`` produces correct ``[^n]`` Markdown footnote format."""
    anchor = CitationAnchor(chunk_id="c1", source_id="s1", text="x")
    assert anchor.to_reference(1) == "[^1]"
    assert anchor.to_reference(42) == "[^42]"
    assert anchor.to_reference(0) == "[^0]"


def test_to_citation_text_full():
    """Citation text includes page and paragraph when present."""
    anchor = CitationAnchor(
        chunk_id="c1", source_id="doc.pdf", text="...", page=3, paragraph=42
    )
    result = anchor.to_citation_text()
    assert "doc.pdf" in result
    assert "pp. 3" in result
    assert "¶42" in result


def test_to_citation_text_no_page_para():
    """Citation text does not crash when page/paragraph are absent."""
    anchor = CitationAnchor(chunk_id="c1", source_id="bare.txt", text="...")
    result = anchor.to_citation_text()
    assert "bare.txt" in result
    assert "pp." not in result
    assert "¶" not in result


def test_to_citation_text_page_only():
    """Citation text with page, no paragraph."""
    anchor = CitationAnchor(
        chunk_id="c1", source_id="doc.md", text="...", page=5, paragraph=None
    )
    result = anchor.to_citation_text()
    assert "pp. 5" in result
    assert "¶" not in result


def test_to_citation_text_paragraph_only():
    """Citation text with paragraph, no page."""
    anchor = CitationAnchor(
        chunk_id="c1", source_id="doc.md", text="...", page=None, paragraph=12
    )
    result = anchor.to_citation_text()
    assert "pp." not in result
    assert "¶12" in result


def test_from_chunk_row_truncates_long_text():
    """Text is truncated to first 200 characters."""
    long_text = "A" * 500
    row = _StubORMChunk(text=long_text)
    anchor = CitationAnchor.from_chunk_row(row)
    assert len(anchor.text) == 200
    assert anchor.text == "A" * 200
