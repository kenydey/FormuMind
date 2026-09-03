"""Chemistry-aware parsing & chunking (KB stream P1).

Covers:
1. Docling tier — first in the auto cascade, markdown + numbered page markers,
   graceful skip when not installed (fake module injection, CI stays light);
2. math atomicity — ``$$…$$`` / ``\\[…\\]`` / ``\\begin{…}`` blocks are never
   split by the chunker (reaction equations survive whole);
3. page provenance — ``<!-- page:N -->`` markers from pypdf/docling are
   consumed into ``Chunk.page_no`` and stripped from chunk text, for both the
   structured and the plain-text (pypdf) paths;
4. MinerU OCR/formula knobs — ``pdf_ocr`` routes to the OCR pipe, enable
   kwargs degrade for older magic-pdf;
5. page_no persists through kb_index into document_chunks rows.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.config import get_settings
from app.services import parsing
from app.services.chunking import Chunk, chunk_markdown


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    parsing._DOCLING_CONVERTERS.clear()
    yield
    parsing._DOCLING_CONVERTERS.clear()
    get_settings.cache_clear()


# ── fake docling ─────────────────────────────────────────────────────────────


def _install_fake_docling(monkeypatch, markdown: str, *, supports_page_break: bool = True):
    base = types.ModuleType("docling")
    datamodel = types.ModuleType("docling.datamodel")
    base_models = types.ModuleType("docling.datamodel.base_models")
    pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    converter_mod = types.ModuleType("docling.document_converter")

    class DocumentStream:
        def __init__(self, name, stream):
            self.name, self.stream = name, stream

    class InputFormat:
        PDF = "pdf"

    class PdfPipelineOptions:
        def __init__(self):
            self.do_formula_enrichment = False

    class PdfFormatOption:
        def __init__(self, pipeline_options=None):
            self.pipeline_options = pipeline_options

    class _Doc:
        def export_to_markdown(self, page_break_placeholder=None):
            if page_break_placeholder is None:
                return markdown.replace("@@PB@@", "")
            if not supports_page_break:
                raise TypeError("unexpected kwarg")
            return markdown.replace("@@PB@@", page_break_placeholder)

    class _Result:
        document = _Doc()

    class DocumentConverter:
        last_options = None

        def __init__(self, format_options=None):
            type(self).last_options = format_options

        def convert(self, source):
            return _Result()

    base_models.DocumentStream = DocumentStream
    base_models.InputFormat = InputFormat
    pipeline_options.PdfPipelineOptions = PdfPipelineOptions
    converter_mod.DocumentConverter = DocumentConverter
    converter_mod.PdfFormatOption = PdfFormatOption
    datamodel.base_models = base_models
    datamodel.pipeline_options = pipeline_options
    base.datamodel = datamodel
    base.document_converter = converter_mod

    for name, mod in {
        "docling": base,
        "docling.datamodel": datamodel,
        "docling.datamodel.base_models": base_models,
        "docling.datamodel.pipeline_options": pipeline_options,
        "docling.document_converter": converter_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return DocumentConverter


def test_docling_is_first_auto_tier_and_numbers_pages(monkeypatch):
    _install_fake_docling(
        monkeypatch, "第一页内容。\n\n@@PB@@\n\n第二页内容。"
    )
    result = parsing.parse_document(b"%PDF-fake", "pdf")
    assert result.parser == "docling"
    assert "<!-- page:1 -->" in result.markdown
    assert "<!-- page:2 -->" in result.markdown
    assert "第二页内容" in result.markdown


def test_docling_formula_enrichment_flag_reaches_options(monkeypatch):
    monkeypatch.setenv("FORMUMIND_PDF_FORMULA_ENRICHMENT", "true")
    get_settings.cache_clear()
    converter_cls = _install_fake_docling(monkeypatch, "内容")
    parsing.parse_document(b"%PDF-fake", "pdf")
    opts = converter_cls.last_options
    assert opts, "format options must be passed"
    (fmt_option,) = opts.values()
    assert fmt_option.pipeline_options.do_formula_enrichment is True


def test_docling_old_api_without_page_break_kwarg(monkeypatch):
    _install_fake_docling(monkeypatch, "旧版输出@@PB@@继续", supports_page_break=False)
    result = parsing.parse_document(b"%PDF-fake", "pdf")
    assert result.parser == "docling"
    assert "旧版输出" in result.markdown
    assert "<!-- page:" not in result.markdown  # old API → no fabricated markers


def test_pdf_cascade_skips_docling_when_absent(monkeypatch):
    # No docling in sys.modules (CI default) → next available tier answers.
    result = parsing.parse_document(b"not really a pdf", "pdf")
    assert result.parser != "docling"


def test_pinned_parser_starts_cascade_at_that_tier(monkeypatch):
    _install_fake_docling(monkeypatch, "docling 输出")
    result = parsing.parse_document(b"%PDF-fake", "pdf", prefer="pypdf")
    assert result.parser != "docling"  # pinned below docling → docling not tried


# ── pypdf page markers ───────────────────────────────────────────────────────


def _install_fake_pypdf(monkeypatch, pages: list[str]):
    mod = types.ModuleType("pypdf")

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class PdfReader:
        def __init__(self, stream):
            self.pages = [_Page(t) for t in pages]

    mod.PdfReader = PdfReader
    monkeypatch.setitem(sys.modules, "pypdf", mod)


def test_pypdf_interleaves_page_markers(monkeypatch):
    _install_fake_pypdf(monkeypatch, ["环氧树脂第一页。", "固化剂第二页。"])
    result = parsing.parse_document(b"%PDF-fake", "pdf", prefer="pypdf")
    assert result.parser == "pypdf"
    assert "<!-- page:1 -->" in result.markdown
    assert "<!-- page:2 -->" in result.markdown


def test_pypdf_empty_pages_stay_none(monkeypatch):
    _install_fake_pypdf(monkeypatch, ["", "  "])
    result = parsing.parse_document(b"%PDF-fake", "pdf", prefer="pypdf")
    assert result.parser != "pypdf" or not result.ok


# ── MinerU knobs ─────────────────────────────────────────────────────────────


def _install_fake_mineru(monkeypatch, *, accepts_enable_kwargs: bool):
    calls: dict = {}
    magic_pdf = types.ModuleType("magic_pdf")
    data = types.ModuleType("magic_pdf.data")
    drw = types.ModuleType("magic_pdf.data.data_reader_writer")
    dataset = types.ModuleType("magic_pdf.data.dataset")
    model = types.ModuleType("magic_pdf.model")
    dabcm = types.ModuleType("magic_pdf.model.doc_analyze_by_custom_model")

    class FileBasedDataWriter:
        def __init__(self, d):
            pass

    class PymuDocDataset:
        def __init__(self, content):
            pass

    class _Result:
        def get_markdown(self, d):
            return "mineru 输出 $$Zn_3(PO_4)_2$$"

    class _Infer:
        def pipe_txt_mode(self, writer):
            calls["pipe"] = "txt"
            return _Result()

        def pipe_ocr_mode(self, writer):
            calls["pipe"] = "ocr"
            return _Result()

    def doc_analyze(ds, ocr=False, **kwargs):
        if kwargs and not accepts_enable_kwargs:
            raise TypeError("unexpected kwargs")
        calls["ocr"] = ocr
        calls["kwargs"] = dict(kwargs)
        return _Infer()

    drw.FileBasedDataWriter = FileBasedDataWriter
    dataset.PymuDocDataset = PymuDocDataset
    dabcm.doc_analyze = doc_analyze
    data.data_reader_writer = drw
    data.dataset = dataset
    model.doc_analyze_by_custom_model = dabcm
    magic_pdf.data = data
    magic_pdf.model = model

    for name, mod in {
        "magic_pdf": magic_pdf,
        "magic_pdf.data": data,
        "magic_pdf.data.data_reader_writer": drw,
        "magic_pdf.data.dataset": dataset,
        "magic_pdf.model": model,
        "magic_pdf.model.doc_analyze_by_custom_model": dabcm,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return calls


def test_mineru_requests_formula_and_table(monkeypatch):
    calls = _install_fake_mineru(monkeypatch, accepts_enable_kwargs=True)
    result = parsing.parse_document(b"%PDF-fake", "pdf", prefer="mineru")
    assert result.parser == "mineru"
    assert calls["kwargs"] == {"formula_enable": True, "table_enable": True}
    assert calls["pipe"] == "txt"


def test_mineru_ocr_flag_switches_pipe(monkeypatch):
    monkeypatch.setenv("FORMUMIND_PDF_OCR", "true")
    get_settings.cache_clear()
    calls = _install_fake_mineru(monkeypatch, accepts_enable_kwargs=True)
    result = parsing.parse_document(b"%PDF-fake", "pdf", prefer="mineru")
    assert result.parser == "mineru"
    assert calls["ocr"] is True
    assert calls["pipe"] == "ocr"


def test_mineru_old_api_degrades(monkeypatch):
    calls = _install_fake_mineru(monkeypatch, accepts_enable_kwargs=False)
    result = parsing.parse_document(b"%PDF-fake", "pdf", prefer="mineru")
    assert result.parser == "mineru"
    assert calls["kwargs"] == {}


# ── math-atomic chunking ─────────────────────────────────────────────────────

EQ = "$$\n2Al + 3H_2SO_4 \\rightarrow Al_2(SO_4)_3 + 3H_2\n$$"


def test_display_math_never_split():
    md = "# 反应机理\n\n" + "前文描述。" * 200 + f"\n\n{EQ}\n\n" + "后文讨论。" * 200
    chunks = chunk_markdown(md, max_chars=400)
    eq_chunks = [c for c in chunks if "\\rightarrow" in c.text]
    assert len(eq_chunks) == 1
    assert eq_chunks[0].text.startswith("$$") and eq_chunks[0].text.endswith("$$")


def test_single_line_math_is_atomic():
    md = "# 公式\n\n说明文字。\n\n$$E = mc^2$$\n\n继续。"
    chunks = chunk_markdown(md, max_chars=100)
    assert any(c.text == "$$E = mc^2$$" for c in chunks)


def test_bracket_and_begin_env_math_atomic():
    md = (
        "# 推导\n\n"
        + "文字。" * 100
        + "\n\n\\[\nK_{sp} = [Zn^{2+}]^3 [PO_4^{3-}]^2\n\\]\n\n"
        + "\\begin{equation}\npH = -\\log[H^+]\n\\end{equation}\n\n"
        + "结尾。"
    )
    chunks = chunk_markdown(md, max_chars=200)
    assert any(c.text.startswith("\\[") and c.text.endswith("\\]") for c in chunks)
    assert any(
        c.text.startswith("\\begin{equation}") and c.text.endswith("\\end{equation}")
        for c in chunks
    )


# ── page provenance ──────────────────────────────────────────────────────────


def test_structured_chunks_carry_page_no_and_strip_markers():
    md = (
        "<!-- page:1 -->\n\n# 专利\n\n## 实施例 1\n\n第一页的实施例内容。\n\n"
        "<!-- page:2 -->\n\n## 实施例 2\n\n第二页的对比样内容。"
    )
    chunks = chunk_markdown(md)
    assert all("<!-- page:" not in c.text for c in chunks)
    by_head = {c.heading_path: c for c in chunks}
    assert by_head["专利 > 实施例 1"].page_no == 1
    assert by_head["专利 > 实施例 2"].page_no == 2


def test_plain_text_pages_split_pagewise():
    md = "<!-- page:1 -->\n\n纯文本第一页。\n\n<!-- page:2 -->\n\n纯文本第二页。"
    chunks = chunk_markdown(md)
    assert [c.page_no for c in chunks] == [1, 2]
    assert all("<!-- page:" not in c.text for c in chunks)


def test_unmarked_plain_text_unchanged():
    chunks = chunk_markdown("无标记纯文本。" * 10)
    assert chunks and all(c.page_no is None for c in chunks)


def test_page_marker_inside_code_fence_is_content():
    md = "# 代码\n\n```\n<!-- page:9 -->\n```\n\n文字。"
    chunks = chunk_markdown(md)
    fence = next(c for c in chunks if c.text.startswith("```"))
    assert "<!-- page:9 -->" in fence.text  # fences are opaque
    assert all(c.page_no != 9 for c in chunks)


# ── page_no persistence through kb_index ─────────────────────────────────────


def test_page_no_persists_to_document_chunks(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore
    from app.services import kb_index

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(source_store_mod, "_store", SourceStore(factory))
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))

    md = (
        "<!-- page:1 -->\n\n# 文档\n\n## 第一节\n\n" + "第一页内容较长。" * 10
        + "\n\n<!-- page:3 -->\n\n## 第三节\n\n" + "第三页内容较长。" * 10
    )
    n = kb_index.index_source("s-page", md, embed=False)
    assert n == 2
    rows = chunk_store_mod._store.get_by_source("s-page")
    assert [r.page_no for r in rows] == [1, 3]
