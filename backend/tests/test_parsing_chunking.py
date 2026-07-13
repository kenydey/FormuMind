"""KB P1 tests — unified parsing layer + structure-aware chunking."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import chunking, parsing


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── parsing layer ────────────────────────────────────────────────────────────


def test_parse_plain_text_formats():
    result = parsing.parse_document("Epoxy resin data sheet 环氧树脂".encode(), "txt")
    assert result.ok
    assert result.parser in ("markitdown", "text")
    assert "环氧树脂" in result.markdown


def test_parse_empty_returns_none_parser():
    result = parsing.parse_document(b"", "pdf")
    assert not result.ok
    assert result.parser == "none"


def test_pdf_tier_order_auto_and_pinned():
    names = [n for n, _ in parsing._pdf_tier_order("auto")]
    assert names == ["docling", "marker", "mineru", "markitdown", "pypdf"]
    names = [n for n, _ in parsing._pdf_tier_order("markitdown")]
    assert names == ["markitdown", "pypdf"]  # pinned + lighter fallbacks only
    names = [n for n, _ in parsing._pdf_tier_order("nonsense")]
    assert names[0] == "docling"  # unknown → auto


def test_pdf_cascade_falls_through_to_working_tier(monkeypatch):
    monkeypatch.setattr(parsing, "_parse_marker", lambda c: None)
    monkeypatch.setattr(parsing, "_parse_mineru", lambda c: None)
    monkeypatch.setattr(parsing, "_parse_markitdown", lambda c, e: None)
    monkeypatch.setattr(parsing, "_parse_pypdf", lambda c: "# Extracted\n\ntext body")
    result = parsing.parse_document(b"%PDF-1.4 fake", "pdf")
    assert result.ok
    assert result.parser == "pypdf"


def test_pdf_parser_setting_pins_tier(monkeypatch):
    monkeypatch.setenv("FORMUMIND_PDF_PARSER", "pypdf")
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(parsing, "_parse_marker", lambda c: calls.append("marker"))
    monkeypatch.setattr(parsing, "_parse_pypdf", lambda c: calls.append("pypdf") or "text out")
    result = parsing.parse_document(b"%PDF fake", "pdf")
    assert calls == ["pypdf"]  # marker never touched
    assert result.parser == "pypdf"


def test_parser_availability_reports_installed_tiers():
    avail = parsing.parser_availability()
    assert set(avail) == {"docling", "marker", "mineru", "markitdown", "pypdf", "trafilatura"}
    assert isinstance(avail["pypdf"], bool)


def test_html_to_markdown_fallback_strips_tags():
    html = "<html><script>evil()</script><body><p>Zinc phosphate primer</p><p>Salt spray 720h</p></body></html>"
    text = parsing.html_to_markdown(html)
    assert "Zinc phosphate primer" in text
    assert "evil" not in text
    assert "<p>" not in text


# ── structure-aware chunking ─────────────────────────────────────────────────

MD_DOC = """# 专利说明书

前言段落，介绍背景技术。

## 实施例

### 实施例 1

环氧树脂 E51 与固化剂 IPDA 按 100:24 混合。

| 组分 | 质量份 |
|------|--------|
| E51  | 100    |
| IPDA | 24     |
| 磷酸锌 | 15   |

### 实施例 2

对比配方使用聚酰胺固化剂。
"""


def test_chunk_markdown_tracks_heading_paths():
    chunks = chunking.chunk_markdown(MD_DOC, max_chars=200)
    paths = {c.heading_path for c in chunks}
    assert "专利说明书" in paths
    assert any("实施例 > 实施例 1" in p for p in paths)
    assert any("实施例 > 实施例 2" in p for p in paths)


def test_chunk_markdown_keeps_tables_atomic():
    chunks = chunking.chunk_markdown(MD_DOC, max_chars=60)  # table > max_chars
    table_chunks = [c for c in chunks if c.text.lstrip().startswith("|")]
    assert len(table_chunks) == 1
    assert "磷酸锌" in table_chunks[0].text  # last row survived intact
    assert table_chunks[0].text.count("|") >= 12


def test_chunk_markdown_keeps_code_fences_atomic():
    md = "# T\n\npre\n\n```python\nx = 1\ny = 2\n```\n\npost"
    chunks = chunking.chunk_markdown(md, max_chars=10)
    fenced = [c for c in chunks if c.text.startswith("```")]
    assert len(fenced) == 1
    assert "x = 1" in fenced[0].text and "y = 2" in fenced[0].text


def test_chunk_markdown_heading_inside_fence_not_a_section():
    md = "# Top\n\n```\n# not a heading\n```\n\ntail text"
    chunks = chunking.chunk_markdown(md)
    assert all("not a heading" not in c.heading_path for c in chunks)


def test_plain_text_degrades_to_legacy_splitter():
    text = "。".join(f"第{i}句话，描述配方细节" for i in range(200))
    chunks = chunking.chunk_markdown(text, max_chars=300)
    assert all(c.heading_path == "" for c in chunks)
    assert all(len(c.text) <= 320 for c in chunks)
    legacy = chunking.chunk_plain_text(text, max_chars=300)
    assert [c.text for c in chunks] == legacy


def test_chunk_plain_text_respects_max_chars():
    text = "word " * 2000
    chunks = chunking.chunk_plain_text(text, max_chars=500)
    assert all(len(c) <= 500 for c in chunks)
    assert sum(len(c) for c in chunks) > 5000  # nothing lost wholesale


# ── ingestion integration ────────────────────────────────────────────────────


def test_to_evidence_carries_heading_path_in_title():
    from app.services.ingestion import _to_evidence

    evidence = _to_evidence(MD_DOC, "patent.md")
    assert evidence
    assert evidence[0].title.startswith("patent")
    joined = " | ".join(e.title for e in evidence)
    assert "实施例" in joined


def test_ingest_file_uses_parsing_layer(monkeypatch):
    from app.services import ingestion

    monkeypatch.setattr(
        "app.services.parsing.parse_document",
        lambda content, ext: parsing.ParseResult("# 报告\n\n" + "环氧体系描述文本。" * 20, "markitdown"),
    )
    outcome = ingestion.ingest_file("report.pdf", b"%PDF fake", persist=False)
    assert outcome.evidence
    assert outcome.evidence[0].source == "local"


def test_pdf_downloader_extract_text_delegates(monkeypatch):
    from app.services import pdf_downloader

    monkeypatch.setattr(
        "app.services.parsing.parse_document",
        lambda content, ext: parsing.ParseResult("full markdown body", "marker"),
    )
    assert pdf_downloader._extract_text(b"%PDF fake") == "full markdown body"


def test_pdf_downloader_raw_fallback_still_works():
    """The dependency-free Tj fallback still catches parse-layer misses."""
    import zlib

    from app.services import pdf_downloader

    stream = zlib.compress(b"BT (Hello patent text) Tj ET")
    pdf = b"%PDF-1.4\nstream\n" + stream + b"\nendstream\n"
    text = pdf_downloader._raw_pdf_stream_text(pdf)
    assert "Hello patent text" in text
