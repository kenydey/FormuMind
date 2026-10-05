"""A PDF with no text layer says what to install when nothing on the machine can OCR it (round-4).

"未能提取到文本——可能是扫描件" is accurate and no help: the upload completes ("文件入库完成：1 条", nothing stored)
and the user is left with a document that will never ingest. When neither the local OCR parser nor MinerU is
available the message now names the fix.
"""
from __future__ import annotations

import pytest

from app.services import ingestion, parsing


@pytest.fixture()
def empty_pdf_parse(monkeypatch):
    monkeypatch.setattr(parsing, "parse_document", lambda content, ext, **kw: parsing.ParseResult("", "none"))
    monkeypatch.setattr(parsing, "can_parse", lambda ext: True)


def _snippet(name: str = "scan.pdf") -> str:
    outcome = ingestion.ingest_file(name, b"%PDF-1.4", persist=False)
    assert outcome.extraction_status == "skipped"
    return outcome.evidence[0].snippet


def test_a_pdf_without_text_and_without_ocr_names_the_fix(empty_pdf_parse, monkeypatch):
    monkeypatch.setattr(parsing, "parser_availability", lambda: {"rapidocr": False, "mineru": False})
    snippet = _snippet()
    assert "扫描件" in snippet
    assert "rapidocr" in snippet and "MinerU" in snippet


@pytest.mark.parametrize("available", [{"rapidocr": True, "mineru": False}, {"rapidocr": False, "mineru": True}])
def test_with_an_ocr_parser_the_hint_is_not_shown(empty_pdf_parse, monkeypatch, available):
    monkeypatch.setattr(parsing, "parser_availability", lambda: available)
    assert "OCR" not in _snippet()


def test_other_formats_get_no_ocr_hint(monkeypatch):
    monkeypatch.setattr(parsing, "parse_document", lambda content, ext, **kw: parsing.ParseResult("", "none"))
    monkeypatch.setattr(parsing, "can_parse", lambda ext: True)
    monkeypatch.setattr(parsing, "parser_availability", lambda: {"rapidocr": False, "mineru": False})
    assert "OCR" not in _snippet("empty.docx")
