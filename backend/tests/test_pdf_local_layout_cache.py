"""The first page of a PDF no longer takes its layout from the PDF parsed before it (round-5; found by the parsing evaluation).

pymupdf's layout model caches the layout it last predicted under ``(doc.name, page.number)``. A document opened from a stream
has no name (``None``), so the first page of any PDF parsed right after a *single-page* one found that page's layout in the
cache and used it for its own: its text was matched against another document's boxes - dropped or read in the wrong order -
and ``assemble`` skipped the blank page without a trace. Parsed a second time the same document came back whole, because by
then the cache held its own last page, which is why it looked like flakiness. A worker that parses uploads one after another
hits it on every upload that follows a one-page PDF.
"""
from __future__ import annotations

import pytest
from app.services import pdf_local

pytest.importorskip("fpdf")
pytestmark = pytest.mark.skipif(not pdf_local.local_available()[0], reason="pymupdf4llm is not installed")


def _pdf(*pages: str, y: float = 120) -> bytes:
    """One line of text per page, ``y`` mm from the top: where it sits decides which layout boxes can hold it."""
    from fpdf import FPDF

    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        pdf.set_y(y)
        pdf.cell(0, 8, text)
    return bytes(pdf.output())


# Its text is at the top of the page; the documents below have theirs mid-page, outside any box the layout of this one holds.
ONE_PAGE = _pdf("A single page: salt spray exposure reached 500 hours.", y=15)
THREE_PAGES = _pdf(
    "Page one: the primer was applied at 60 microns dry film.",
    "Page two: salt spray exposure reached 500 hours.",
    "Page three: adhesion was checked by cross-hatch after immersion.",
)
OTHER_THREE = _pdf("Cover of another report.", "Its second page.", "Its third page.")


def _markdown(content: bytes) -> list[str]:
    pages = pdf_local.extract_pages(content)
    assert pages is not None
    return [p.markdown for p in pages]


def test_each_document_is_opened_under_a_name_of_its_own(monkeypatch):
    import pymupdf4llm

    seen: list[str | None] = []

    def to_markdown(doc, **kwargs):
        seen.append(doc.name)
        return [{"text": "x"} for _ in range(doc.page_count)]

    monkeypatch.setattr(pymupdf4llm, "to_markdown", to_markdown)
    for content in (ONE_PAGE, THREE_PAGES, ONE_PAGE):
        pdf_local.extract_pages(content)
    assert all(seen), seen  # None is what a stream gets without one - and what every document used to share
    assert seen[0] == seen[2] != seen[1]


def test_a_page_predicted_without_ocr_is_not_reused_for_the_same_page_read_with_it():
    assert pdf_local._layout_name(ONE_PAGE, ocr=False) != pdf_local._layout_name(ONE_PAGE, ocr=True)
    assert pdf_local._layout_name(ONE_PAGE, ocr=False) == pdf_local._layout_name(ONE_PAGE, ocr=False)


def test_the_first_page_does_not_depend_on_which_document_came_before():
    _markdown(ONE_PAGE)  # leaves ("", 0) in the layout cache: the collision this test is about
    after_a_one_page_pdf = _markdown(THREE_PAGES)
    _markdown(OTHER_THREE)  # leaves page 2 in the cache, which cannot collide with page 0
    after_a_longer_pdf = _markdown(THREE_PAGES)
    assert after_a_one_page_pdf == after_a_longer_pdf
    assert all(after_a_one_page_pdf), "a page came back blank"


def test_a_one_page_pdf_after_another_one_page_pdf_is_read_on_its_own_layout():
    other = _pdf("A different single page about adhesion, parsed second.")  # mid-page, as above
    _markdown(THREE_PAGES)  # the cache now holds a page 2: nothing for a first page to collide with
    clean = _markdown(other)
    _markdown(THREE_PAGES)
    _markdown(ONE_PAGE)  # leaves its own page 0 in the cache
    assert _markdown(other) == clean
