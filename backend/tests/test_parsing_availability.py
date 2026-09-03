"""Parser capability must be observable, and its absence must be loud.

The image installs no optional parser: `requirements.txt` carried none and the
Dockerfile installs only `.[llm]`. Every tier therefore returned None,
`parse_document` returned an empty string, and the upload answered 200 with a
bland "无法提取文本内容" placeholder — indistinguishable from a scanned page.
A deployment could accept uploads for weeks and index nothing.

These tests pin the three things that make that impossible to repeat: the
declarations actually install working backends, a missing parser is a 422 and
not a 200, and /health says so before a user has to notice.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import parsing

BACKEND = Path(__file__).resolve().parents[1]


# ── declarations ─────────────────────────────────────────────────────────────


def test_markitdown_is_declared_with_its_format_backends() -> None:
    """MarkItDown keeps every converter behind an extra. Declared bare it
    imports fine and converts nothing: .pptx/.xlsx yield an empty document and
    .docx silently degrades to python-docx, which drops tables."""
    pyproject = (BACKEND / "pyproject.toml").read_text()
    spec = re.search(r'"(markitdown[^"]*)"', pyproject)
    assert spec, "markitdown must be declared in pyproject"
    for extra in ("pdf", "docx", "pptx", "xlsx"):
        assert extra in spec.group(1), f"markitdown missing [{extra}]: {spec.group(1)}"


def test_dependency_catalog_installs_the_same_extras() -> None:
    """The one-click installer must not install a weaker markitdown than the
    one pyproject declares — it is the path most users actually take."""
    from app.services.dependencies import CATALOG

    entry = next(d for d in CATALOG if d.pip_name == "markitdown")
    for extra in ("pdf", "docx", "pptx", "xlsx"):
        assert extra in entry.install_spec, entry.install_spec


def test_a_pdf_parser_ships_in_the_locked_requirements() -> None:
    """Optional extras are fine for the good parsers, but the image must not
    ship with zero. pypdf is pure Python and a few hundred KB."""
    requirements = (BACKEND / "requirements.txt").read_text()
    assert re.search(r"^pypdf==", requirements, re.M), requirements


# ── capability reporting ─────────────────────────────────────────────────────


def test_format_availability_covers_every_ingestible_format() -> None:
    formats = parsing.format_availability()
    assert set(formats) == {"pdf", "docx", "pptx", "xlsx", "html", "text"}
    assert all(isinstance(v, bool) for v in formats.values())


def test_importable_markitdown_is_not_mistaken_for_a_working_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blind spot that caused this: `import markitdown` succeeding says
    nothing about whether any converter is installed."""
    monkeypatch.setattr(
        parsing, "optional_import", lambda mod: mod == "markitdown"
    )
    assert parsing._markitdown_can("pptx") is False
    assert parsing.format_availability()["pptx"] is False


def test_a_present_backend_makes_the_format_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        parsing, "optional_import", lambda mod: mod in ("markitdown", "pptx")
    )
    assert parsing.format_availability()["pptx"] is True


def test_plain_text_formats_never_need_a_library() -> None:
    for ext in ("txt", "md", "csv", "html", "json", "xml"):
        assert parsing.can_parse(ext), ext


def test_legacy_doc_is_reported_unparseable() -> None:
    """mammoth and python-docx both read .docx only. Claiming otherwise sends
    the user hunting for a parser that cannot exist locally."""
    assert parsing.can_parse("doc") is False
    assert ".docx" in parsing.install_hint("doc")


@pytest.mark.parametrize("ext", ["pdf", "docx", "pptx", "xlsx"])
def test_install_hints_are_actionable(ext: str) -> None:
    hint = parsing.install_hint(ext)
    assert hint and hint != f"未安装可处理 .{ext} 的解析器。"
    assert "pip install" in hint or "依赖管理" in hint


# ── the failure is loud ──────────────────────────────────────────────────────


def test_missing_parser_raises_rather_than_returning_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ingestion

    monkeypatch.setattr(parsing, "can_parse", lambda ext: False)
    monkeypatch.setattr(
        parsing, "parse_document", lambda c, e, **kw: parsing.ParseResult("", "none")
    )
    with pytest.raises(parsing.ParserUnavailable) as exc:
        ingestion.ingest_file("spec.pdf", b"%PDF-1.4 fake")
    assert exc.value.ext == "pdf"
    assert exc.value.hint


def test_upload_with_no_parser_is_a_422_not_a_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """422 with a fixable message, not 200 with an empty knowledge base."""
    from app.api import ingest as ingest_api

    def explode(*_args, **_kwargs):
        raise parsing.ParserUnavailable("pdf", "未安装任何 PDF 解析器")

    monkeypatch.setattr(ingest_api, "ingest_file", explode)
    response = TestClient(app).post(
        "/api/ingest", files={"file": ("spec.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert response.status_code == 422
    assert "解析器" in response.json()["detail"]


def test_empty_extraction_from_a_working_parser_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scanned PDF is a document problem, not a deployment problem. It must
    keep degrading to a placeholder rather than raising."""
    from app.services import ingestion

    monkeypatch.setattr(parsing, "can_parse", lambda ext: True)
    monkeypatch.setattr(
        parsing, "parse_document", lambda c, e, **kw: parsing.ParseResult("", "none")
    )
    outcome = ingestion.ingest_file("scan.pdf", b"%PDF-1.4 fake")
    assert outcome.extraction_status == "skipped"
    assert len(outcome.evidence) == 1
    assert "扫描件" in outcome.evidence[0].snippet


def test_one_unparseable_file_does_not_sink_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import ingestion

    def selective(name: str, content: bytes, **_kwargs):
        if name.endswith(".doc"):
            raise parsing.ParserUnavailable("doc", "旧版 .doc 无本地解析器")
        return ingestion.IngestOutcome(
            evidence=[
                ingestion.Evidence(
                    source="local", identifier=name, title=name,
                    snippet="body", relevance=0.9,
                )
            ]
        )

    monkeypatch.setattr(ingestion, "ingest_file", selective)
    outcome = ingestion.ingest_files_batch(
        [("a.pdf", b"x"), ("legacy.doc", b"y"), ("c.pdf", b"z")]
    )
    assert len(outcome.evidence) == 3          # nothing dropped
    snippets = " ".join(e.snippet for e in outcome.evidence)
    assert "旧版 .doc" in snippets              # and the reason is named


# ── health ───────────────────────────────────────────────────────────────────


def test_health_reports_parser_availability() -> None:
    body = TestClient(app).get("/health").json()
    assert "parsers" in body
    assert set(body["parsers"]) == {"pdf", "docx", "pptx", "xlsx", "html", "text"}


def test_health_is_degraded_when_no_pdf_parser_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: an install that cannot read a PDF must not look 'ok'."""
    monkeypatch.setattr(
        parsing,
        "format_availability",
        lambda: {"pdf": False, "docx": False, "pptx": False,
                 "xlsx": False, "html": True, "text": True},
    )
    body = TestClient(app).get("/health").json()
    assert body["parsers"]["pdf"] is False
    assert body["status"] == "degraded"


# ── real round trips (skipped when the backends are absent) ──────────────────


@pytest.fixture()
def docx_with_table(tmp_path: Path) -> bytes:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("环氧防腐配方", 1)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "组分"
    table.cell(0, 1).text = "wt%"
    table.cell(1, 0).text = "Zinc phosphate"
    table.cell(1, 1).text = "12.5"
    path = tmp_path / "s.docx"
    document.save(path)
    return path.read_bytes()


def test_docx_tables_survive_the_markitdown_path(docx_with_table: bytes) -> None:
    """The tables *are* the data in a formulation document."""
    pytest.importorskip("mammoth")
    result = parsing.parse_document(docx_with_table, "docx")
    assert result.parser == "markitdown"
    assert "Zinc phosphate" in result.markdown
    assert "12.5" in result.markdown


def test_the_python_docx_fallback_loses_tables(docx_with_table: bytes) -> None:
    """Why the extras matter, stated as a fact rather than a warning: the
    fallback returns the heading and drops every cell. A .docx parsed this way
    looks successful and contains none of the numbers."""
    text = parsing._parse_docx(docx_with_table) or ""
    assert "环氧防腐配方" in text          # heading survives
    assert "Zinc phosphate" not in text    # the table does not
    assert "12.5" not in text


def test_pptx_speaker_notes_and_titles_are_extracted(tmp_path: Path) -> None:
    pptx = pytest.importorskip("pptx")
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "耐盐雾 1000 小时"
    slide.notes_slide.notes_text_frame.text = "备注：ASTM B117"
    path = tmp_path / "s.pptx"
    presentation.save(path)

    result = parsing.parse_document(path.read_bytes(), "pptx")
    assert result.ok, "a bare markitdown install parses pptx to nothing"
    assert "耐盐雾" in result.markdown


def test_xlsx_cells_are_extracted(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["指标", "数值"])
    sheet.append(["salt_spray_hours", 1080])
    path = tmp_path / "s.xlsx"
    workbook.save(path)

    result = parsing.parse_document(path.read_bytes(), "xlsx")
    assert result.ok
    assert "1080" in result.markdown


def test_html_boilerplate_is_stripped() -> None:
    """trafilatura's job. The regex fallback also strips tags, so this holds
    either way — what must never happen is navigation text reaching the index."""
    html = (
        "<html><body><nav>首页 产品 联系我们</nav>"
        "<article><h1>环氧富锌底漆</h1><p>耐盐雾 1080 小时，VOC 240 g/L。</p></article>"
        "<footer>版权所有 2026</footer></body></html>"
    )
    text = parsing.html_to_markdown(html)
    assert "环氧富锌底漆" in text
    assert "1080" in text


# ── garbage must not reach the index ─────────────────────────────────────────


def test_markitdown_passthrough_of_binary_is_rejected() -> None:
    """MarkItDown's plain-text converter accepts anything it does not
    recognise and hands the input straight back. Three control bytes came back
    as three control characters and were indexed as prose — a "successful"
    parse that also stopped the cascade before pypdf got a turn."""
    pytest.importorskip("markitdown")
    assert parsing._parse_markitdown(bytes([0, 1, 2]), "bin") is None
    assert parsing.parse_document(bytes([0, 1, 2]), "bin").parser == "none"


def test_markitdown_echoing_a_broken_pdf_is_rejected() -> None:
    """A malformed PDF returned its own header as the 'extracted text', which
    both polluted the index and denied pypdf the chance to try."""
    pytest.importorskip("markitdown")
    assert parsing._parse_markitdown(b"%PDF-1.4 fake", "pdf") is None


@pytest.mark.parametrize(
    "text,binary",
    [
        ("环氧防腐配方，耐盐雾 1080 小时", False),
        ("# Heading\n\n| a | b |\n| --- | --- |\n", False),
        ("\x00\x01\x02\x03", True),
        ("ok\ttab\r\nnewline are fine", False),
        ("", True),
    ],
)
def test_binary_detection_keeps_real_text(text: str, binary: bool) -> None:
    assert parsing._looks_like_undecoded_binary(text) is binary


def test_every_pdf_tier_can_be_patched() -> None:
    """markitdown was the one tier stored by direct reference, so a test that
    monkeypatched it was silently ignored — the patch applied to the module
    attribute the registry never consulted."""
    import inspect

    for name, fn in parsing._PDF_TIERS:
        source = inspect.getsource(fn)
        assert "lambda" in source, f"{name} tier is not late-bound"
