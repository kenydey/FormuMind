"""Phase 1: MinerU cloud first-class backend.

Covers:
- strategy routing: prefer="mineru" pins the cloud tier first with
  local fallback; the auto order is unchanged (third-party upload stays
  opt-in).
- the §8.2 trap fix: enable_formula/enable_table are sent explicitly to the
  SDK (which only sends them when set) — the cloud equivalent of the local
  pipeline's mfr_enable assertion.
- structured products: tables → extraction_tables, formulas →
  extraction_formulas; block markers drive Chunk.block_type in the chunker.
- formula gatekeeper: empty-latex and regex catches are counted, LLM off
  by default.
- fail-open: cloud unavailable / clean document (adaptive skip) / call
  failure all fall through to the next tier without raising.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.extraction_store import ExtractionStore
from app.services import chunking, mineru_cloud, mineru_structured, parsing
from app.services.chunking import block_marker, chunk_markdown, page_marker


def _block(kind: str, **kw) -> mineru_cloud.MinerUBlock:
    return mineru_cloud.MinerUBlock(type=kind, **kw)


def _doc(*blocks: mineru_cloud.MinerUBlock) -> mineru_cloud.MinerUDocument:
    return mineru_cloud.MinerUDocument(blocks=list(blocks))


def _clean_pdf(pages: int = 2, chars: int = 200) -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is in the parse_pro extra")

    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), "x" * chars)
    out = doc.tobytes()
    doc.close()
    return out


def _dirty_pdf() -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is in the parse_pro extra")

    doc = fitz.open()
    doc.new_page()  # no text layer at all
    out = doc.tobytes()
    doc.close()
    return out


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


# ── routing ────────────────────────────────────────────────────────────────


def test_auto_order_keeps_mineru_slot() -> None:
    # The default chain is structurally unchanged: the mineru tier keeps its
    # 4th slot. Unconfigured it returns None immediately (same as the dead
    # magic_pdf tier it replaced), so auto behaviour is identical.
    names = [n for n, _ in parsing._pdf_tier_order("auto")]
    assert names == ["hybrid", "docling", "marker", "mineru", "rapidocr",
                    "markitdown", "pypdf"]


def test_prefer_mineru_pins_first_with_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    order = parsing._pdf_tier_order("mineru")
    assert order[0][0] == "mineru"
    names = [n for n, _ in parsing._PDF_TIERS]
    rest = [n for n, _ in order[1:]]
    assert rest == names[names.index("mineru") + 1:]


def test_a_pin_on_mineru_that_cannot_run_keeps_the_whole_cascade(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pinning a tier drops everything above it; pinning one that cannot run (switched off, no token, no SDK) would
    drop hybrid, Docling and marker for nothing. The 高配 profile pinned the retired local path this way, and every
    install that had applied it kept parsing PDFs with MarkItDown / pypdf only."""
    import logging

    monkeypatch.setattr(parsing, "_mineru_usable", lambda: False)
    monkeypatch.setattr(parsing, "_dead_mineru_pin_warned", False)
    with caplog.at_level(logging.WARNING, logger=parsing.logger.name):
        first = [n for n, _ in parsing._pdf_tier_order("mineru")]
        second = [n for n, _ in parsing._pdf_tier_order("mineru")]
    assert first == second == [n for n, _ in parsing._PDF_TIERS]
    warnings = [r for r in caplog.records if "MinerU cloud tier cannot run" in r.getMessage()]
    assert len(warnings) == 1, "said once, not on every document"


def test_prefer_mineru_wins_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _doc(
        _block("text", page_idx=0, text="cloud text"),
        _block("equation", page_idx=0, text="E=mc^2"),
    )
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(
        mineru_structured, "_doc_is_clean", lambda content: False
    )
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes", lambda content, **kw: doc
    )
    result = parsing.parse_document(b"%PDF fake", "pdf", prefer="mineru")
    assert result.parser == "mineru"
    assert result.structured is not None
    assert "cloud text" in result.markdown


def test_mineru_unavailable_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prefer="mineru" tries [mineru, rapidocr, markitdown, pypdf]; with the
    # cloud down the tier returns None and the cascade below it still runs.
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (False, "off"))
    monkeypatch.setattr(
        parsing, "_parse_rapidocr", lambda content: "local text"
    )
    result = parsing.parse_document(b"%PDF fake", "pdf", prefer="mineru")
    assert result.parser == "rapidocr"
    assert result.markdown == "local text"


# ── §8.2 trap: formula/table sent explicitly ────────────────────────────────


def _fake_mineru_module(calls: list):
    mod = types.ModuleType("mineru")

    class FakeResult:
        state = "done"
        content_list = []
        markdown = ""
        images = []

    class FakeClient:
        def __init__(self, token=None, base_url=None):
            pass

        def extract(self, path, **kw):
            calls.append(kw)
            return FakeResult()

    mod.MinerU = FakeClient
    mod.MinerUError = type("MinerUError", (Exception,), {})
    mod.AuthError = type("AuthError", (mod.MinerUError,), {})
    mod.QuotaExceededError = type("QuotaExceededError", (mod.MinerUError,), {})
    mod.FileTooLargeError = type("FileTooLargeError", (mod.MinerUError,), {})
    mod.PageLimitError = type("PageLimitError", (mod.MinerUError,), {})
    mod.TimeoutError = type("TimeoutError", (mod.MinerUError,), {})
    return mod


def test_extract_sends_formula_and_table_explicitly(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "mineru", _fake_mineru_module(calls))
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    p = tmp_path / "d.pdf"
    p.write_bytes(b"%PDF fake")
    mineru_cloud._extract(b"%PDF fake", ext="pdf", ocr=False)
    assert calls, "SDK extract was not called"
    # The trap: the SDK only sends these when explicitly set. We always set.
    assert calls[0].get("formula") is True
    assert calls[0].get("table") is True


def test_extract_respects_explicit_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "mineru", _fake_mineru_module(calls))
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    mineru_cloud._extract(b"%PDF fake", ext="pdf", ocr=False, formula=False)
    assert calls[0].get("formula") is False


# ── adaptive skip ──────────────────────────────────────────────────────────


def test_clean_document_skips_cloud_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list = []
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes", lambda content, **kw: calls.append(kw)
    )
    out = mineru_structured.parse_structured(_clean_pdf())
    assert out is None
    assert calls == [], "clean pages must not cost a cloud call"


def test_dirty_document_calls_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    monkeypatch.setattr(
        mineru_cloud,
        "parse_bytes",
        lambda content, **kw: _doc(_block("text", page_idx=0, text="ocr text")),
    )
    out = mineru_structured.parse_structured(_dirty_pdf())
    assert out is not None
    assert "ocr text" in out.markdown


# ── structured persistence ─────────────────────────────────────────────────


def test_tables_and_formulas_persist(
    monkeypatch: pytest.MonkeyPatch, factory
) -> None:
    import app.db.database as _db

    monkeypatch.setattr(_db, "default_session_factory", lambda: factory)
    doc = _doc(
        _block("text", page_idx=0, text="intro"),
        _block(
            "table",
            page_idx=0,
            html="<table><tr><td>a</td><td>b</td></tr>"
            "<tr><td>1</td><td>2</td></tr></table>",
            caption="性能对比",
        ),
        _block("equation", page_idx=1, text="$$x^2$$ (1)"),
    )
    structured = mineru_structured.MinerUStructured(
        markdown=mineru_structured.render_markdown(
            mineru_structured._normalise_blocks(doc)
        ),
        blocks=mineru_structured._normalise_blocks(doc),
    )
    mineru_structured.persist_structured("src-1", structured)
    store = ExtractionStore(factory)
    tables = store.tables_for_source("src-1")
    formulas = store.formulas_for_source("src-1")
    assert len(tables) == 1
    assert "| a | b |" in tables[0].markdown_text
    assert tables[0].caption == "性能对比"
    assert tables[0].page_no == 1
    assert len(formulas) == 1
    assert "x^2" in formulas[0].latex
    assert formulas[0].formula_no == "1"
    assert formulas[0].page_no == 2


def test_persist_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # persist_structured imports default_session_factory lazily; point the
    # database module at a factory that raises — the call must not propagate.
    import app.db.database as _db

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(_db, "default_session_factory", _boom)
    structured = mineru_structured.MinerUStructured(markdown="x", blocks=[])
    mineru_structured.persist_structured("src-1", structured)  # must not raise


# ── block markers → chunk block_type ───────────────────────────────────────


def test_block_markers_drive_chunk_block_type() -> None:
    md = "\n\n".join(
        [
            page_marker(1),
            block_marker("text"),
            "Some running text.",
            block_marker("table"),
            "| a | b |\n|---|---|\n| 1 | 2 |",
            page_marker(2),
            block_marker("formula"),
            "$$x^2$$",
        ]
    )
    chunks = chunk_markdown(md)
    kinds = [c.block_type for c in chunks]
    assert "table" in kinds
    assert "formula" in kinds
    assert not any("<!-- block:" in c.text for c in chunks), "markers stripped"
    assert all(c.page_no in (1, 2) for c in chunks if c.page_no)


def test_html_table_to_markdown() -> None:
    md = mineru_structured._html_table_to_markdown(
        "<table><tr><th>a</th><th>b</th></tr>"
        "<tr><td>1</td><td>2</td></tr></table>"
    )
    assert "| a | b |" in md
    assert "| --- | --- |" in md
    assert "| 1 | 2 |" in md


# ── formula gatekeeper ─────────────────────────────────────────────────────


def test_gatekeeper_counts_empty_latex() -> None:
    mineru_structured.reset_gatekeeper_stats()
    structured = mineru_structured.MinerUStructured(
        markdown="x",
        blocks=[
            mineru_structured.StructuredBlock(
                page_no=1, kind="formula", text="(image only)", latex=""
            )
        ],
    )
    report = mineru_structured.formula_gatekeeper(structured)
    assert report.empty_latex_blocks == 1
    assert mineru_structured.gatekeeper_stats()["empty_latex"] == 1


def test_gatekeeper_regex_catch() -> None:
    mineru_structured.reset_gatekeeper_stats()
    structured = mineru_structured.MinerUStructured(
        markdown="x",
        blocks=[
            mineru_structured.StructuredBlock(
                page_no=1, kind="text", text="反应式为 \\ce{H2SO4} 如下"
            )
        ],
    )
    report = mineru_structured.formula_gatekeeper(structured)
    assert report.regex_catches == 1
    assert report.llm_fixed == 0  # LLM off by default


def test_gatekeeper_llm_off_by_default() -> None:
    from app.config import get_settings

    assert get_settings().formula_gatekeeper_llm_enabled is False


def test_gatekeeper_never_raises() -> None:
    structured = mineru_structured.MinerUStructured(markdown="", blocks=[])
    mineru_structured.formula_gatekeeper(structured)  # must not raise
