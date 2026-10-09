"""Unified document parsing layer — every byte stream becomes Markdown here.

Single entry point (``parse_document``) used by file upload, URL ingestion and
the full-text fetcher, replacing the per-caller parser cascades.  Parsers are
pluggable and probed at call time:

* **PDF**: hybrid (pymupdf4llm + 云端 MinerU 按页升级 + 本地 OCR，CPU-cheap 主路径)
  → docling → marker → mineru(云端) → rapidocr → markitdown → pypdf,
  order controlled by ``FORMUMIND_PDF_PARSER`` (``auto`` tries best-first; naming
  a parser pins it with fallback to the tiers below it).  hybrid 已内置本地布局
  解析 + 本地 OCR + 云端 MinerU，后面的 docling / marker 是离线高保真降级
  （dormant stub：未安装时跳过，docling/marker 需 weights、CPU 极慢）；
  本地 magic-pdf 路径已退役（见 ``_parse_mineru``）；
  markitdown / pypdf 是纯文本兜底。
* **Other formats** (DOCX/XLSX/PPTX/HTML/…): MarkItDown → format-specific
  fallbacks (python-docx, plain text decode). XLSX goes through an openpyxl
  table reader first (titles, blank rows and merged cells are what a pandas
  header guess gets wrong), then MarkItDown.
* **Page provenance**: Docling and pypdf interleave ``<!-- page:N -->``
  markers; the chunker consumes them into ``Chunk.page_no`` and strips them.

Every parser is optional; the layer degrades tier by tier and reports which
parser produced the output so provenance can be persisted.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

from ..config import get_settings
from .errors import log_handled_exception, optional_import

logger = logging.getLogger(__name__)

# Extensions handled by the plain-text decoder, and therefore always parseable.
_ALWAYS_PARSEABLE = frozenset({"txt", "md", "csv", "html", "htm", "json", "xml"})


class ParserUnavailable(RuntimeError):
    """No parser is installed for this format.

    Distinct from "the document holds no text": one is a deployment that needs
    a package, the other is a scanned file. Collapsing them into an empty
    string is how an upload came to report success while indexing nothing.
    """

    def __init__(self, ext: str, hint: str) -> None:
        super().__init__(hint)
        self.ext = ext
        self.hint = hint


@dataclass
class ParseResult:
    markdown: str
    parser: str  # tier name: hybrid | docling | marker | mineru | rapidocr | markitdown | openpyxl | pypdf | docx | text | none
    # W2-3: structured table assets extracted after a successful parse
    # (table_contract.TableAsset). Empty when extraction is disabled/failed.
    tables: list = field(default_factory=list)
    # Phase 1: layout-aware structured products (MinerUStructured) from the
    # mineru tier — blocks with page/kind, tables, formulas. None for the
    # text chain. Consumed by ingestion → extraction_tables/formulas.
    structured: object = None
    # v29 Phase3 D-1: 表格抽取可观测性 —— table_stats 记录抽取数/成功数/警告数，
    # 便于发现静默丢表问题（如 H-6 类）。
    table_stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.markdown.strip())


def _maybe_extract_tables(result: ParseResult, content: bytes) -> ParseResult:
    """W2-3 wiring: table-contract extraction on the successful parse path.

    Fail-open by contract: any error leaves ``result`` untouched (tables=[]).
    Extracted assets are kept in-memory on ``result.tables``; the sidecar JSON
    is persisted separately by :func:`persist_table_sidecar` once the
    ``SourceDocument`` row exists, keyed by the source UUID — the same key the
    read side (``table_contract.load_tables``) uses. (Historically this
    function wrote the sidecar keyed by the file-bytes sha256, which no reader
    ever used, so those writes were silently unreadable. Pre-fix sidecars are
    orphaned on disk and can be deleted.)
    Gate: ``table_extract_enabled`` (default True).

    W3-1: extracted assets are additionally normalised into PropertySets
    (``table_normalize``) and persisted under the ``"property_sets"`` key of
    the same sidecar JSON — also fail-open.
    """
    try:
        if not result.ok:
            return result
        if not getattr(get_settings(), "table_extract_enabled", True):
            return result
        from . import table_contract as _tc
        blocks = _tc.blocks_from_markdown(result.markdown or "")
        # v29 Phase3 D-1: 表格可观测性 —— 记录抽取统计
        stats = {
            "blocks_found": len(blocks),
            "tables_extracted": 0,
            "warnings": [],
        }
        if not blocks:
            result.table_stats = stats
            return result
        assets = _tc.extract_tables("", blocks, parser=result.parser)
        result.tables = assets
        stats["tables_extracted"] = len(assets)
        # 警告：发现块但未抽出表格（可能静默丢表）
        if blocks and not assets:
            stats["warnings"].append("发现表格块但未抽出资产，可能解析失败")
            logger.warning("table_contract: %d blocks 但 0 assets（parser=%s）", len(blocks), result.parser)
        result.table_stats = stats
    except Exception:
        logger.exception("table_contract: extraction failed (fail-open)")
    return result


def persist_table_sidecar(source_id: str, tables: list) -> None:
    """Persist W2-3/W3-1 table assets as JSON sidecar keyed by source UUID.

    Must be called after the ``SourceDocument`` row exists. Fail-open: never
    raises. The key MUST be the source UUID — readers call
    ``load_tables(doc.id)``; any other key makes the payload unreadable.
    """
    if not source_id or not tables:
        return
    try:
        from . import table_contract as _tc
        from . import table_normalize as _tn
        # Re-key assets to the real source UUID now that it exists.
        for i, asset in enumerate(tables):
            asset.source_id = source_id
            asset.table_id = f"{source_id}#p{(asset.page_no or 0):02d}-{i:02d}"
        try:
            prop_dicts = [p.to_dict() for p in _tn.normalize_tables(tables)]
        except Exception:
            logger.exception("table_normalize: failed (fail-open)")
            prop_dicts = None
        _tc.save_tables(source_id, tables, property_sets=prop_dicts)
    except Exception:
        logger.exception("table sidecar persist failed (fail-open)")


def maybe_persist_table_sidecar(source_id: str | None, parsed) -> None:
    """P2: 各入库路径统一的表格 sidecar 落盘入口（fail-open）。

    调用方在 SourceDocument 行落盘后、持有 ``parse_document`` 的
    ``parsed.tables`` 时调用。fulltext/专利路径在 persist 时手头没有
    PDF 字节（fetch 阶段已丢弃），暂不做重解析 —— 如需覆盖，需把
    fetch 的返回签名改成 (text, tables) 再一路透传。
    """
    if not source_id or parsed is None or not getattr(parsed, "tables", None):
        return
    try:
        persist_table_sidecar(source_id, parsed.tables)
    except Exception:  # noqa: BLE001 - fail-open
        logger.exception("table sidecar persist failed (fail-open)")


# ── individual parsers (return markdown/text or None) ────────────────────────

_DOCLING_CONVERTERS: dict[str, object] = {}
_DOCLING_PAGE_BREAK = "<!-- docling-page-break -->"


def _parse_docling(content: bytes) -> str | None:
    """Docling (IBM): layout/table-aware PDF → Markdown; formulas → LaTeX.

    The converter (layout + TableFormer models) is cached per process.  When
    the installed docling supports formula enrichment and
    ``FORMUMIND_PDF_FORMULA_ENRICHMENT`` is on, display equations come back as
    LaTeX ``$$…$$`` blocks — which the chunker keeps atomic.
    """
    try:
        import io as _io

        from docling.datamodel.base_models import DocumentStream, InputFormat  # type: ignore
        from docling.document_converter import DocumentConverter, PdfFormatOption  # type: ignore

        key = "conv"
        if key not in _DOCLING_CONVERTERS:
            format_options = {}
            try:
                from docling.datamodel.pipeline_options import PdfPipelineOptions  # type: ignore

                opts = PdfPipelineOptions()
                if hasattr(opts, "do_formula_enrichment"):
                    opts.do_formula_enrichment = bool(
                        get_settings().pdf_formula_enrichment
                    )
                format_options[InputFormat.PDF] = PdfFormatOption(pipeline_options=opts)
            except Exception:  # options API drift — plain defaults still work
                format_options = {}
            _DOCLING_CONVERTERS[key] = (
                DocumentConverter(format_options=format_options)
                if format_options
                else DocumentConverter()
            )
        converter = _DOCLING_CONVERTERS[key]
        result = converter.convert(
            DocumentStream(name="document.pdf", stream=_io.BytesIO(content))
        )
        doc = result.document
        try:
            md = doc.export_to_markdown(
                page_break_placeholder=f"\n\n{_DOCLING_PAGE_BREAK}\n\n"
            )
        except TypeError:  # older docling without the placeholder kwarg
            md = doc.export_to_markdown()
        if not md or not md.strip():
            return None
        return _number_page_breaks(md)
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "docling parse failed")
        return None


def _number_page_breaks(md: str) -> str:
    """Turn docling's uniform page-break placeholder into numbered markers."""
    from .chunking import page_marker

    parts = md.split(_DOCLING_PAGE_BREAK)
    if len(parts) <= 1:
        return md
    out = [f"{page_marker(1)}\n\n{parts[0].strip()}"]
    for i, part in enumerate(parts[1:], start=2):
        out.append(f"{page_marker(i)}\n\n{part.strip()}")
    return "\n\n".join(out)


_MARKER_MODELS: dict[str, object] = {}


def _parse_marker(content: bytes) -> str | None:
    """marker-pdf: layout-aware PDF → Markdown (optional heavy extra)."""
    try:
        import tempfile

        from marker.converters.pdf import PdfConverter  # type: ignore
        from marker.models import create_model_dict  # type: ignore
        from marker.output import text_from_rendered  # type: ignore

        if "models" not in _MARKER_MODELS:
            _MARKER_MODELS["models"] = create_model_dict()
        converter = PdfConverter(artifact_dict=_MARKER_MODELS["models"])
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            rendered = converter(tmp.name)
        text, _, _ = text_from_rendered(rendered)
        return text or None
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "marker parse failed")
        return None


def _parse_mineru(content: bytes):
    """MinerU first-class backend (Phase 1): cloud SDK, structured.

    The old magic-pdf local path is retired — ``magic_pdf`` was never
    installed in this deployment (verified 2026-09-28), so this tier was a
    dead branch. The real backend is the MinerU cloud SDK: formula/table
    recognition is explicitly enabled (see ``mineru_cloud._extract_options``
    — the cloud equivalent of the local pipeline's mfr_enable trap, §8.2).

    Returns a ParseResult carrying the structured blocks (tables/formulas
    land in ``extraction_tables``/``extraction_formulas`` via ingestion);
    None when the cloud is unavailable, unconfigured, or the document is
    clean (adaptive skip) — the cascade then falls through, fail-open.

    Uploading to mineru.net is a third-party transfer: this tier only fires
    when ``mineru_enabled`` is set with a token. In the auto order it sits
    in the same 4th position the dead tier occupied — the default chain is
    structurally unchanged; unconfigured it behaves exactly as before
    (returns None immediately).
    """
    from . import mineru_structured

    structured = mineru_structured.parse_structured(content)
    if structured is None or not structured.markdown.strip():
        return None
    return ParseResult(structured.markdown, "mineru", structured=structured)


def _looks_like_undecoded_binary(text: str) -> bool:
    """Whether *text* is raw bytes wearing a string costume.

    MarkItDown's plain-text converter accepts anything it does not recognise
    and hands the input straight back: ``b"\\x00\\x01\\x02"`` returns as three
    control characters, and a malformed PDF returns its own header. Passing
    that on is worse than returning nothing — it looks like a successful parse,
    it stops the cascade before a real parser gets a turn, and the bytes end up
    embedded in the retrieval index as if they were prose.
    """
    if not text:
        return True
    sample = text[:2000]
    unprintable = sum(1 for ch in sample if ch < " " and ch not in "\t\r\n")
    return unprintable / len(sample) > 0.05


def _parse_markitdown(content: bytes, ext: str) -> str | None:
    try:
        from markitdown import MarkItDown  # type: ignore

        result = MarkItDown().convert_stream(io.BytesIO(content), file_extension=ext)
        text = result.text_content or None
        if text and _looks_like_undecoded_binary(text):
            logger.debug("markitdown returned undecoded bytes for .%s — declining", ext)
            return None
        # The check above only catches unprintable output. Passthrough of
        # *printable* junk slips past it — a malformed PDF returns the ASCII
        # "%PDF-1.4 fake" quite happily — so also decline output that is
        # byte-for-byte the input. A converter that hands back what it was
        # given has not parsed anything.
        if text and ext not in _ALWAYS_PARSEABLE:
            try:
                if text.strip() == content.decode("utf-8", "ignore").strip():
                    logger.debug("markitdown echoed the input for .%s — declining", ext)
                    return None
            except Exception:  # pragma: no cover - decode guard only
                pass
        return text
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "markitdown parse failed")
        return None


def _parse_pypdf(content: bytes) -> str | None:
    try:
        import pypdf  # type: ignore

        from .chunking import page_marker

        reader = pypdf.PdfReader(io.BytesIO(content))
        parts = [
            f"{page_marker(i + 1)}\n\n{page.extract_text() or ''}"
            for i, page in enumerate(reader.pages)
        ]
        text = "\n\n".join(parts)
        # Marker-only output means no extractable text at all.
        stripped = "\n".join(
            l for l in text.split("\n") if not l.strip().startswith("<!-- page:")
        )
        return text if stripped.strip() else None
    except ImportError:
        return None
    except BaseException as exc:
        # pypdf can surface Rust panics (BaseException) from broken crypto backends.
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            raise
        logger.info("pypdf parse failed: %s", exc)
        return None


def _docx_table_to_markdown(table) -> str:
    """Render a python-docx table as a pipe table (one block, no surrounding text)."""
    rows: list[list[str]] = []
    for row in table.rows:
        rows.append(
            [c.text.strip().replace("|", "\\|").replace("\n", " ") for c in row.cells]
        )
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "|" + "|".join("---" for _ in rows[0]) + "|",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(lines)


def _docx_walk_elements(
    parent, para_by_el: dict, tbl_by_el: dict, depth: int = 0
) -> list[str]:
    """v29 Phase2 M-5/M-6/L-11: DOCX 递归 walker。

    处理嵌套表（表内表）、SDT 内容控件（w:sdt）、文本框（w:txbxContent）。
    depth 防无限递归（上限 10）。
    """
    if depth > 10:
        return []
    parts: list[str] = []
    for child in parent:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            # 段落：也检查是否含文本框（w:txbxContent）
            t = para_by_el.get(child, "")
            if t.strip():
                parts.append(t.strip())
            # 文本框内容（L-11）
            for txbx in child.iter():
                ttag = txbx.tag.split("}")[-1] if "}" in txbx.tag else txbx.tag
                if ttag == "txbxContent":
                    for p_el in txbx:
                        ptag = p_el.tag.split("}")[-1] if "}" in p_el.tag else p_el.tag
                        if ptag == "p":
                            pt = para_by_el.get(p_el, "")
                            if pt.strip() and pt.strip() not in parts:
                                parts.append(f"[文本框] {pt.strip()}")
        elif tag == "tbl":
            t = tbl_by_el.get(child)
            if t is not None:
                md = _docx_table_to_markdown(t)
                if md:
                    parts.append(md)
                # 嵌套表（M-5）：检查单元格内的嵌套表格
                # python-docx 的 table.cells 可能含嵌套表，需通过 _tbl 查找
                try:
                    for row in t.rows:
                        for cell in row.cells:
                            for nested in cell._tc.iter():
                                ntag = nested.tag.split("}")[-1] if "}" in nested.tag else nested.tag
                                if ntag == "tbl" and nested is not child:
                                    nt = tbl_by_el.get(nested)
                                    if nt is not None:
                                        nmd = _docx_table_to_markdown(nt)
                                        if nmd and nmd not in parts:
                                            parts.append(f"[嵌套表]\n{nmd}")
                except Exception:
                    pass
        elif tag == "sdt":
            # SDT 内容控件（M-6）：递归处理 sdtContent
            for sdt_content in child:
                ctag = sdt_content.tag.split("}")[-1] if "}" in sdt_content.tag else sdt_content.tag
                if ctag == "sdtContent":
                    parts.extend(_docx_walk_elements(sdt_content, para_by_el, tbl_by_el, depth + 1))
    return parts


# v29 Phase1 M-7/M-3: zip 炸弹防护 —— OOXML（docx/xlsx）是 zip，
# 恶意小文件可解压出 GB 级内容。统一检查未压缩总大小。
_ZIP_BOMB_MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 100MB


def _check_zip_bomb(content: bytes, label: str) -> bool:
    """True=安全，False=疑似 zip 炸弹（已打日志）。"""
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            total = sum(i.file_size for i in zf.infolist())
            if total > _ZIP_BOMB_MAX_UNCOMPRESSED:
                logger.warning(
                    "%s 疑似 zip 炸弹：未压缩 %d MB > %d MB，拒绝解析",
                    label, total // (1024 * 1024),
                    _ZIP_BOMB_MAX_UNCOMPRESSED // (1024 * 1024),
                )
                return False
        return True
    except Exception:
        # 非 zip 文件，交由调用方处理
        return True


def _parse_docx(content: bytes) -> str | None:
    # v29 Phase1 M-7: DOCX zip 炸弹检查
    if not _check_zip_bomb(content, "docx"):
        return None
    try:
        import docx  # type: ignore

        doc = docx.Document(io.BytesIO(content))
        # v28 P-1: 按文档顺序交错提取段落与表格 —— 此前只读 doc.paragraphs，
        # 表格被静默丢弃。body 层 w:p/w:tbl 按出现顺序走；表内段落不在
        # body 层出现，不会重复计入。
        # v29 Phase2: 改用递归 walker（_docx_walk_elements），处理嵌套表/SDT/文本框。
        para_by_el = {p._p: p.text for p in doc.paragraphs}
        tbl_by_el = {t._tbl: t for t in doc.tables}
        parts = _docx_walk_elements(doc.element.body, para_by_el, tbl_by_el)
        text = "\n\n".join(parts)
        return text if text.strip() else None
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "docx parse failed")
        return None


def _parse_xlsx(content: bytes) -> str | None:
    """Lightweight openpyxl fallback when markitdown[xlsx] is not installed."""
    # v29 Phase1 M-3: 复用 zip 炸弹检查（相邻 _parse_xlsx_tables 有，未复用）
    if not _check_zip_bomb(content, "xlsx"):
        return None
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets:
            # v29 Phase1 H-2: 维度门禁 —— 稀疏大维度文件（如 A1:XFD1048576）
            # 会导致 ~1.7×10¹⁰ 次单元格访问，hang 住 ingest 线程。
            # 必须在 iter_rows 之前检查。
            try:
                dims = (sheet.max_row or 0) * (sheet.max_column or 0)
            except Exception:
                dims = 0
            if dims > _XLSX_TABLES_MAX_CELLS:
                logger.warning(
                    "xlsx sheet '%s' 维度 %d cells 超限 %d，跳过",
                    sheet.title, dims, _XLSX_TABLES_MAX_CELLS,
                )
                lines.append(f"## {sheet.title}（表格过大，已跳过）")
                continue
            lines.append(f"## {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if any(x.strip() for x in cells):
                    lines.append("\t".join(cells))
        text = "\n".join(lines).strip()
        return text or None
    except Exception as exc:
        log_handled_exception(logger, exc, "xlsx parse failed")
        return None


# A workbook is read cell by cell, so its size is bounded: past these a file goes to the converters below, which stream.
_XLSX_TABLES_MAX_BYTES = 10 * 1024 * 1024
_XLSX_TABLES_MAX_SHEET_BYTES = 40 * 1024 * 1024  # uncompressed sheet XML: a small file can declare a million formatted rows
_XLSX_TABLES_MAX_CELLS = 400_000


def _xlsx_cell_text(value) -> str:
    """A cell as a reader sees it: no ``35.0`` for a stored 35, no float noise, nothing that would end a table row."""
    import datetime
    from decimal import Decimal

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        if value == 0:
            return "0"
        return format(Decimal(repr(round(value, 12))).normalize(), "f")
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ", timespec="minutes") if (value.hour or value.minute or value.second) else value.date().isoformat()
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return " ".join(str(value).split())


def _xlsx_grid(sheet) -> list[list[str]]:
    """The sheet's cells as text, merged ranges filled with the value of their top-left cell."""
    grid = [[_xlsx_cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
    for merged in sheet.merged_cells.ranges:
        if merged.min_row > len(grid):
            continue
        top_left = grid[merged.min_row - 1][merged.min_col - 1] if merged.min_col <= len(grid[merged.min_row - 1]) else ""
        for r in range(merged.min_row, min(merged.max_row, len(grid)) + 1):
            row = grid[r - 1]
            for c in range(merged.min_col, min(merged.max_col, len(row)) + 1):
                row[c - 1] = top_left
    return grid


def _xlsx_block_markdown(rows: list[list[str]]) -> list[str]:
    """One block of consecutive non-blank rows: the rows above and below the table become lines of text, the rest a table."""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    distinct = [{c for c in r if c} for r in rows]
    multi = [i for i, d in enumerate(distinct) if len(d) >= 2]
    if not multi:  # a title, a note: no columns to speak of
        return [" ".join(dict.fromkeys(c for c in r if c)) for r in rows]
    first, last = multi[0], multi[-1]
    lines = [" ".join(dict.fromkeys(c for c in r if c)) for r in rows[:first]]  # a title above the table, once, not per column
    body = rows[first:last + 1]
    used = [c for c in range(width) if any(r[c] for r in body)]
    body = [[r[c].replace("|", "\\|") for c in used] for r in body]
    lines.append("")
    lines.append("| " + " | ".join(body[0]) + " |")
    lines.append("| " + " | ".join(["---"] * len(used)) + " |")
    lines.extend("| " + " | ".join(r) + " |" for r in body[1:])
    lines.append("")
    lines.extend(" ".join(dict.fromkeys(c for c in r if c)) for r in rows[last + 1:])  # notes under the table
    return lines


def _parse_xlsx_tables(content: bytes) -> str | None:
    """Workbook -> Markdown with the tables a person sees: a pipe table per block of rows, titles and notes as lines.

    MarkItDown reads a sheet through pandas, which takes the first row for the header. A formulation sheet starts with a
    merged title ("水性环氧底漆配方表（单位：wt%）") and a blank row, so its header became ``Unnamed: 1``, a row of ``NaN`` followed,
    the real header was demoted to a data row, and every empty cell read ``NaN`` - in the text that is indexed and in the
    table the contract normalises into properties. Measured by evals/suites/parsing.py (xlsx-title-row).

    Blocks are the runs of non-blank rows; within one, rows above the first row with two different values and below the last
    one are titles and notes (a merged title is one line, not one per column), the rest is the table, its first row the
    header. Empty columns go; merged ranges are filled with their value. A two-level header (a group label merged over
    sub-headers) stays two rows: the second reads as the first data row.

    Returns None for a file too large to hold cell by cell (the streaming converters take it), for anything unreadable, and
    when openpyxl is missing, so the cascade moves on.
    """
    if len(content) > _XLSX_TABLES_MAX_BYTES:
        return None
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return None
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist() if i.filename.startswith("xl/worksheets/")) > _XLSX_TABLES_MAX_SHEET_BYTES:
                return None
        workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        if sum((ws.max_row or 0) * (ws.max_column or 0) for ws in workbook.worksheets) > _XLSX_TABLES_MAX_CELLS:
            return None
        parts: list[str] = []
        any_cell = False
        for sheet in workbook.worksheets:
            parts.append(f"## {sheet.title}")
            block: list[list[str]] = []
            for row in [*_xlsx_grid(sheet), []]:  # the empty row closes the last block
                if any(row):
                    block.append(row)
                elif block:
                    parts.extend(_xlsx_block_markdown(block))
                    parts.append("")
                    any_cell, block = True, []
        if not any_cell:
            return None
        return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()
    except Exception as exc:
        log_handled_exception(logger, exc, "xlsx table parse failed")
        return None


def _parse_text_file(content: bytes, ext: str) -> str | None:
    """Text-like uploads. An HTML page is markup, not text: URL ingestion has always converted it with
    ``html_to_markdown``, but an uploaded ``.html`` / ``.htm`` (both in the upload dialog's accept list) was stored
    as raw source — ``<script>`` and ``<style>`` bodies, navigation, every tag — and chunked as if it were prose."""
    text = _parse_plain(content)
    if text is not None and ext in ("html", "htm"):
        return html_to_markdown(text)
    return text


def _parse_plain(content: bytes) -> str | None:
    import codecs

    def _printable_ratio(text: str) -> float:
        if not text:
            return 0.0
        return sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t") / len(text)

    def _utf16_tiebreak(b: bytes, new_enc: str, cur_enc: str | None) -> bool:
        """v16 P3-2: LE/BE 可打印率平局时裁决。

        策略：
        1. NUL 位置优先：LE 的 NUL 在奇数位，BE 的 NUL 在偶数位。
           这对 ASCII 最可靠（CJK 的高字节也可能为 0x00，但 NUL 模式更稳定）。
        2. CJK 高字节 (0x4E-0x9F) 集中位置为次要信号。
        仅当信号明确时才推翻 LE 优先；否则保持 LE（Windows 现实）。
        """
        if len(b) < 4 or len(b) % 2:
            return False
        odd = b[1::2]
        even = b[0::2]
        odd_nul = sum(1 for x in odd if x == 0) / max(1, len(odd))
        even_nul = sum(1 for x in even if x == 0) / max(1, len(even))
        # NUL 模式明确：奇数位多 NUL → LE；偶数位多 NUL → BE
        if odd_nul >= 0.5 and odd_nul > even_nul + 0.2:
            # 明确是 LE
            return "le" in new_enc and "be" in (cur_enc or "")
        if even_nul >= 0.5 and even_nul > odd_nul + 0.2:
            # 明确是 BE
            return "be" in new_enc and "le" in (cur_enc or "")
        return False

    # 1. BOM sniffing: an explicit BOM is authoritative. The "utf-16"/"utf-32"
    # codecs (no endian suffix) consume and strip the BOM.
    for bom, enc in (
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
        (codecs.BOM_UTF8, "utf-8-sig"),
    ):
        if content.startswith(bom):
            try:
                return content.decode(enc)
            except Exception:
                break
    # 2. NUL bytes: almost certainly UTF-16/32 without a BOM. Must come before
    # utf-8, because utf-8 "successfully" decodes NUL bytes into garbage.
    # v13-2: 四个候选全解码取 printable ratio 最高者（LE 平局优先，Windows
    # 现实），替代"首个过 0.7 即返回"——无 BOM 的 BE 不再被系统性误判为 LE。
    if b"\x00" in content:
        # v16 P3-3: 极短输入（<8B）按 NUL 位置直判端序 ——
        # LE 的 NUL 在奇数位，BE 的 NUL 在偶数位。
        if len(content) < 8:
            odd_nul = sum(1 for i in range(1, len(content), 2) if content[i] == 0)
            even_nul = sum(1 for i in range(0, len(content), 2) if content[i] == 0)
            if odd_nul > even_nul:
                try:
                    return content.decode("utf-16-le")
                except Exception:
                    pass
            elif even_nul > odd_nul:
                try:
                    return content.decode("utf-16-be")
                except Exception:
                    pass
            # NUL 数量持平则走下通用路径
        best, best_ratio = None, 0.0
        best_enc = None
        for enc in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
            try:
                text = content.decode(enc)
            except Exception:
                continue
            r = _printable_ratio(text)
            if text and r > best_ratio:
                best, best_ratio, best_enc = text, r, enc
            elif text and r == best_ratio and best is not None:
                # v16 P3-2: 平局裁决 —— 纯 ASCII 下 LE/BE 可打印率相同，
                # 用 CJK 高字节位置模式裁决（LE: 奇数位集中 0x4E-0x9F）。
                if _utf16_tiebreak(content, enc, best_enc):
                    best, best_enc = text, enc
        if best and best_ratio > 0.7:
            return best
    # 3. Plain utf-8.
    # v13-2: 删除旧 path3 的 C0 启发式 reinterpret——正常 UTF-8 文本含 \x0c
    # 等控制符即触发 utf-16 重解码是设计错误（其目标场景已被 path2 覆盖）。
    # utf-8 成功即返回，不再二次猜测。
    try:
        return content.decode("utf-8")
    except Exception:
        pass
    # 3b. v13-2: utf-8 失败后、gbk 之前，utf-16 高阈值兜底——覆盖"无 BOM
    # 纯 CJK"（无 NUL）。必须校验字节模式，否则 GBK 中文字节流会被误判
    # 为 UTF-16（两者解码都出高可打印率 CJK）。UTF-16-LE 的 CJK 文本：
    # 奇数位字节集中在 0x4E-0x9F（U+4E00-U+9FFF 的高字节）；BE 则偶数位。
    # 短输入（<8 字节）模式无统计意义（如 café 的 latin-1 字节偶然匹配），
    # 跳过启发式走 gbk/latin-1 保守路径。
    def _looks_like_utf16le(b: bytes) -> bool:
        if len(b) < 8 or len(b) % 2:
            return False
        odd = b[1::2]
        return sum(1 for x in odd if 0x4E <= x <= 0x9F) / len(odd) >= 0.7

    def _looks_like_utf16be(b: bytes) -> bool:
        if len(b) < 8 or len(b) % 2:
            return False
        even = b[0::2]
        return sum(1 for x in even if 0x4E <= x <= 0x9F) / len(even) >= 0.7

    for enc, check in (("utf-16-le", _looks_like_utf16le),
                       ("utf-16-be", _looks_like_utf16be)):
        if not check(content):
            continue
        try:
            text = content.decode(enc)
        except Exception:
            continue
        if text and _printable_ratio(text) >= 0.95:
            return text
    # 4. Fallbacks. latin-1 never fails, so it stays last.
    # v21-fix: gbk → gb18030（超集，能解更多生僻字）。
    for enc in ("gb18030", "latin-1"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return None


# ── registry ─────────────────────────────────────────────────────────────────

def _parse_hybrid(content: bytes) -> str | None:
    """Local layout-aware extraction, with optional per-page cloud escalation.

    First tier because it is both the fastest and, on a CPU-only host, the
    best: docling and marker need weights this deployment cannot afford
    (marker measures ~54 s/page on CPU), and local MinerU needs a GPU. When
    pymupdf4llm is absent this returns None and the cascade continues, so the
    default install behaves exactly as before.
    """
    from . import hybrid_parse

    return hybrid_parse.parse(content)


def _parse_rapidocr(content: bytes) -> str | None:
    """Local OCR for a scanned PDF — the last resort that actually reads pixels.

    Sits below the cloud tier and above the text-layer ones. That position is
    deliberate: everything above it either reads an existing text layer (which a
    scan does not have) or gives real layout structure, and everything below it
    returns nothing at all on a scan. Before this, a scanned PDF on a deployment
    without a MinerU token fell through the entire cascade to the "可能是扫描件"
    placeholder and nothing was indexed.
    """
    from . import rapidocr_local

    return rapidocr_local.ocr_pdf(content)


# Every entry wraps its parser in a lambda so the name resolves at call time.
# markitdown used to be held by direct reference, which made it the one tier a
# test could not monkeypatch — the patch was accepted and silently ignored.
_PDF_TIERS: tuple[tuple[str, object], ...] = (
    ("hybrid", lambda c, e: _parse_hybrid(c)),
    ("docling", lambda c, e: _parse_docling(c)),
    ("marker", lambda c, e: _parse_marker(c)),
    ("mineru", lambda c, e: _parse_mineru(c)),
    ("rapidocr", lambda c, e: _parse_rapidocr(c)),
    ("markitdown", lambda c, e: _parse_markitdown(c, e)),
    ("pypdf", lambda c, e: _parse_pypdf(c)),
)


# Non-PDF tiers, in the same shape as _PDF_TIERS. This used to be a hardcoded
# if-chain, which meant every new fallback had to be wedged into the control
# flow rather than registered alongside its peers.
#
# The plain-text tier goes first on purpose: for formats that are already text
# (txt/md/csv/html/htm/json/xml) there is nothing for markitdown to convert,
# and markitdown mis-decodes non-ASCII bytes (e.g. UTF-8 Chinese) into mojibake
# that slips past the binary-output guard. _parse_plain tries utf-8 → gbk →
# latin-1 and returns the text untouched. Binary formats (docx/xlsx) fall
# through the text tier (ext not in _ALWAYS_PARSEABLE) to markitdown unchanged.
_DOC_TIERS: tuple[tuple[str, object], ...] = (
    ("text", lambda c, e: _parse_text_file(c, e) if e in _ALWAYS_PARSEABLE else None),
    ("openpyxl", lambda c, e: _parse_xlsx_tables(c) if e in ("xlsx", "xlsm") else None),
    ("markitdown", lambda c, e: _parse_markitdown(c, e)),
    ("docx", lambda c, e: _parse_docx(c) if e in ("docx", "doc") else None),
    ("xlsx", lambda c, e: _parse_xlsx(c) if e in ("xlsx", "xlsm") else None),
)


_dead_mineru_pin_warned = False


def _mineru_usable() -> bool:
    """Whether the cloud MinerU tier can run now: switched on, token configured, SDK importable."""
    from .mineru_cloud import mineru_available

    return mineru_available()[0]


def _pdf_tier_order(prefer: str) -> list[tuple[str, object]]:
    global _dead_mineru_pin_warned
    tiers = list(_PDF_TIERS)
    if prefer in ("auto", ""):
        return tiers
    names = [n for n, _ in tiers]
    if prefer not in names:
        logger.warning("unknown FORMUMIND_PDF_PARSER=%r — using auto order", prefer)
        return tiers
    if prefer == "mineru" and not _mineru_usable():
        # Pinning a tier means "this one first, then the lighter ones below it" - so pinning the cloud tier while it cannot
        # run also dropped hybrid, Docling and marker, the best local parsers. The "高配" profile did exactly that (it
        # pinned the long-retired local magic-pdf path), and every install that had applied it kept parsing PDFs with
        # MarkItDown / pypdf only.
        if not _dead_mineru_pin_warned:
            _dead_mineru_pin_warned = True
            logger.warning(
                "FORMUMIND_PDF_PARSER=mineru but the MinerU cloud tier cannot run (disabled, no token or no SDK): "
                "using the auto order. Set FORMUMIND_PDF_PARSER=auto to silence this."
            )
        return tiers
    idx = names.index(prefer)
    # Pinned parser first, then the lighter tiers below it as fallback.
    return tiers[idx:]


def parse_document(content: bytes, ext: str, *, prefer: str | None = None) -> ParseResult:
    """Parse *content* (with file extension *ext*, no dot) into Markdown/text.

    Timed here rather than at each call site: this is the one entry point every
    format goes through, and it is the only place that knows which tier
    actually won — a fact ``ParseResult.parser`` returns and callers discard.
    Attributing a slow ingest needs both numbers together.
    """
    from . import ingest_timing as timing

    ext = (ext or "").lower().lstrip(".")
    if not content:
        return ParseResult("", "none")

    # v29 Phase4 D-2: 文档级规模守卫 —— 2000 页 PDF ≈ 6 分钟 + 350MB，
    # 无守卫会拖死 worker。超限直接拒绝（fail-fast），而非慢死。
    _max_mb = float(getattr(get_settings(), "parse_max_file_mb", 200) or 200)
    if len(content) > _max_mb * 1024 * 1024:
        logger.warning(
            "parse_document: 文件 %.1fMB 超上限 %.0fMB，拒绝解析",
            len(content) / (1024 * 1024), _max_mb,
        )
        r = ParseResult("", "none")
        r.table_stats = {"error": f"文件超限（{_max_mb:.0f}MB）"}
        return r
    if ext == "pdf":
        _max_pages = int(getattr(get_settings(), "parse_max_pdf_pages", 2000) or 2000)
        try:
            from . import pdf_local
            n = pdf_local.page_count(content)
            if n > _max_pages:
                logger.warning("parse_document: PDF %d 页超上限 %d，拒绝解析", n, _max_pages)
                r = ParseResult("", "none")
                r.table_stats = {"error": f"PDF 页数超限（{_max_pages}）"}
                return r
        except Exception:
            pass  # 页数获取失败，交由解析链处理

    with timing.span("parse"):
        if ext == "pdf":
            order = _pdf_tier_order(prefer if prefer is not None else get_settings().pdf_parser)
            for name, fn in order:
                # v18-15: tier 异常不杀整篇 —— 记 note 后继续下一 tier。
                # 违背 fail-open 承诺是数据丢失级 bug。
                try:
                    out = fn(content, ext)
                except Exception as exc:  # noqa: BLE001
                    timing.note(parser=f"{name}:error")  # v22: 与非 PDF 路径对齐
                    logger.warning("parse tier %s failed: %s", name, exc)
                    continue
                # Tiers may return a full ParseResult (mineru_cloud) or plain
                # text; both are accepted so existing tiers stay untouched.
                if isinstance(out, ParseResult):
                    result = out
                    text = out.markdown
                else:
                    text = out
                    result = None
                if text and text.strip():
                    timing.note(parser=name)
                    final = result if result is not None else ParseResult(text, name)
                    return _maybe_extract_tables(final, content)
            timing.note(parser="none")
            return ParseResult("", "none")

        for name, fn in _DOC_TIERS:
            # v19-1: 非 PDF tier 加 try/except（与 v18-15 同构），
            # 异常记 note 后继续降级，不杀整篇。
            try:
                text = fn(content, ext)
            except Exception as exc:  # noqa: BLE001
                timing.note(parser=f"{name}:error")
                logger.warning("doc tier %s failed for %s: %s", name, ext, exc)
                continue
            if text and text.strip():
                timing.note(parser=name)
                return _maybe_extract_tables(ParseResult(text, name), content)
        timing.note(parser="none")
        return ParseResult("", "none")


def html_to_markdown(html: str) -> str:
    """Web page body → Markdown: trafilatura (boilerplate-free, keeps tables)
    with the legacy regex tag-stripper as fallback."""
    try:
        import trafilatura  # type: ignore

        text = trafilatura.extract(
            html,
            include_tables=True,
            include_links=False,
            favor_recall=True,
            output_format="markdown",
        )
        if text and len(text.strip()) > 100:
            # trafilatura keeps a table as a table on an article-sized page, but on a small one (a datasheet is
            # mostly its table) it drops to a plain-text baseline that puts every cell on its own line. A data
            # table that came out without a pipe row is the converter below's job.
            if "|---" in text or "| ---" in text or not _has_data_table(html):
                return text
    except ImportError:
        pass
    except Exception as exc:
        log_handled_exception(logger, exc, "trafilatura extract failed")

    text = _strip_script_style(html)
    text = _html_tables_to_pipes(text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"<[^<>]+>", " ", text)  # not [^>]+: an unclosed "<" then rescans the rest of the page, once per "<"
    text = _collapse_whitespace_before_newline(text)
    return re.sub(r"[ \t]+", " ", text).strip()


# The tag stripper below runs on whatever HTML a user uploads or a URL returns. Its regexes were written as
# ``<(script|style).*?>.*?</\1>`` / ``<table\b.*?</table\s*>`` / ``\s+\n``: lazy or greedy runs that, when the closing part
# is missing, are retried from every start — a truncated download with one unclosed <script> took minutes, 300 KB of
# whitespace took 2.5 (measured). These helpers do the same work in a single pass over the text.

_SCRIPT_STYLE_OPEN = re.compile(r"(?i)<(script|style)\b[^<>]*>")
_SCRIPT_STYLE_CLOSE = {
    "script": re.compile(r"(?i)</script\s*>"),
    "style": re.compile(r"(?i)</style\s*>"),
}
_TABLE_OPEN = re.compile(r"(?i)<table\b")
_TABLE_CLOSE = re.compile(r"(?i)</table\s*>")


def _strip_script_style(html: str) -> str:
    """Drop ``<script>`` / ``<style>`` elements (an unclosed one is left for the tag stripper)."""
    out: list[str] = []
    pos = 0
    no_closer: set[str] = set()  # tags with no closing tag anywhere after the point we have reached
    for opener in _SCRIPT_STYLE_OPEN.finditer(html):
        if opener.start() < pos:
            continue  # inside an element already dropped
        name = opener.group(1).lower()
        if name in no_closer:
            continue
        closer = _SCRIPT_STYLE_CLOSE[name].search(html, opener.end())
        if closer is None:
            no_closer.add(name)  # none after this opener means none after any later one
            continue
        out.append(html[pos:opener.start()])
        out.append(" ")
        pos = closer.end()
    out.append(html[pos:])
    return "".join(out)


def _table_spans(html: str):
    """``(start, end)`` of each ``<table>…</table>`` (outer-most first closer, as the old regex matched)."""
    pos = 0
    for opener in _TABLE_OPEN.finditer(html):
        if opener.start() < pos:
            continue
        closer = _TABLE_CLOSE.search(html, opener.end())
        if closer is None:
            return  # no closing tag after this table means none after any later one
        yield opener.start(), closer.end()
        pos = closer.end()


def _collapse_whitespace_before_newline(text: str) -> str:
    """``re.sub(r"\\s+\\n", "\\n", text)`` without its quadratic backtracking on a long run with no newline.

    A whitespace run that contains a newline becomes that newline plus whatever followed the run's last newline.
    """
    def repl(match: re.Match) -> str:
        run = match.group(0)
        last = run.rfind("\n")
        return run if last < 0 else "\n" + run[last + 1:]

    return re.sub(r"\s+", repl, text)


def _has_data_table(html: str) -> bool:
    """A ``<table>`` that holds data (two rows, two columns, a number) rather than page layout."""
    from .table_contract import _is_numeric_cell, _parse_html_table

    for start, end in _table_spans(html):
        headers, rows = _parse_html_table(html[start:end])
        body = ([headers] if headers else []) + rows
        if len(body) >= 2 and max(len(r) for r in body) >= 2 and any(_is_numeric_cell(c) for r in body for c in r):
            return True
    return False


def _html_tables_to_pipes(html: str) -> str:
    """``<table>`` → a Markdown pipe table, before the tag stripper flattens its cells into one line of words.

    Without trafilatura (an optional extra) a datasheet page lost every row and column boundary — the one part
    the table contract, the chunker and the normalizer can use.
    """
    from .table_contract import _parse_html_table

    def _pipe(fragment: str) -> str:
        headers, rows = _parse_html_table(fragment)
        if not headers and not rows:
            return " "
        width = max([len(headers)] + [len(r) for r in rows])
        pad = lambda cells: [c.replace("|", "\\|") for c in cells] + [""] * (width - len(cells))  # noqa: E731
        lines = ["| " + " | ".join(pad(headers)) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines += ["| " + " | ".join(pad(r)) + " |" for r in rows]
        return "\n\n" + "\n".join(lines) + "\n\n"

    out: list[str] = []
    pos = 0
    for start, end in _table_spans(html):
        out.append(html[pos:start])
        out.append(_pipe(html[start:end]))
        pos = end
    out.append(html[pos:])
    return "".join(out)


def _local_ocr_installed() -> bool:
    """Whether the local OCR engine can be built: the package ``rapidocr_local`` imports and the runtime under it."""
    from .rapidocr_local import REQUIRED_MODULES

    return all(optional_import(module) for module in REQUIRED_MODULES)


def parser_availability() -> dict[str, bool]:
    """Which parser tiers are importable (for the dependencies UI)."""
    return {
        "hybrid": optional_import("pymupdf4llm"),
        "docling": optional_import("docling"),
        "marker": optional_import("marker"),
        # Phase 1: the mineru tier is the cloud SDK backend — "available" now
        # means SDK importable AND enabled with a token (the old magic_pdf
        # local path was never installed; availability key set is unchanged).
        "mineru": optional_import("mineru") and bool(get_settings().mineru_enabled),
        "rapidocr": _local_ocr_installed(),
        "markitdown": optional_import("markitdown"),
        "pypdf": optional_import("pypdf"),
        "trafilatura": optional_import("trafilatura"),
    }


# MarkItDown ships every format backend as an optional extra, so importing
# `markitdown` proves nothing about what it can convert. These are the modules
# each of its converters actually needs.
_MARKITDOWN_BACKENDS: dict[str, tuple[str, ...]] = {
    "pdf": ("pdfminer", "pdfplumber"),
    "docx": ("mammoth",),
    "pptx": ("pptx",),
    "xlsx": ("openpyxl",),
}


def _markitdown_can(fmt: str) -> bool:
    if not optional_import("markitdown"):
        return False
    return any(optional_import(mod) for mod in _MARKITDOWN_BACKENDS.get(fmt, ()))


def format_availability() -> dict[str, bool]:
    """Whether each input format can actually be parsed right now.

    Distinct from ``parser_availability`` on purpose: that reports which
    libraries import, this reports which *formats* survive the round trip. A
    bare MarkItDown install imports fine and converts nothing, so the two
    answers genuinely differ — and it is this one that decides whether an
    upload can succeed.
    """
    return {
        "pdf": any(
            (
                optional_import("pymupdf4llm"),
                optional_import("docling"),
                optional_import("marker"),
                # Local OCR reads scans, which none of the text-layer parsers can.
                _local_ocr_installed(),
                _markitdown_can("pdf"),
                optional_import("pypdf"),
            )
        ),
        "docx": _markitdown_can("docx") or optional_import("docx"),
        "pptx": _markitdown_can("pptx"),
        "xlsx": _markitdown_can("xlsx") or optional_import("openpyxl"),
        # trafilatura only improves HTML; the regex stripper always works.
        "html": True,
        "text": True,
    }


def can_parse(ext: str) -> bool:
    """Whether *ext* has any working parser. Used to tell a configuration
    problem apart from a document that genuinely holds no text."""
    ext = (ext or "").lower().lstrip(".")
    if ext in _ALWAYS_PARSEABLE:
        return True
    availability = format_availability()
    if ext in ("doc",):
        # Legacy binary .doc: neither mammoth nor python-docx reads it.
        return False
    return availability.get(ext, False)


_INSTALL_HINTS: dict[str, str] = {
    "pdf": "未安装任何 PDF 解析器。请在「设置 → 依赖管理」安装 markitdown 或 pypdf"
           "（服务器端：pip install -e '.[file_ingest]'）。",
    "docx": "未安装 DOCX 解析器。请在「设置 → 依赖管理」安装 markitdown"
            "（pip install 'markitdown[docx]'；仅装 python-docx 会丢失表格）。",
    "pptx": "未安装 PPTX 解析器。markitdown 需带 pptx 后端："
            "pip install 'markitdown[pptx]'。",
    "xlsx": "未安装 XLSX 解析器。markitdown 需带 xlsx 后端："
            "pip install 'markitdown[xlsx]'。",
    "doc": "旧版 .doc 二进制格式无本地解析器（mammoth 与 python-docx 均只支持 .docx）。"
           "请另存为 .docx，或启用 MinerU 云端解析。",
}


def install_hint(ext: str) -> str:
    """Actionable Chinese hint for a format with no working parser."""
    ext = (ext or "").lower().lstrip(".")
    return _INSTALL_HINTS.get(ext, f"未安装可处理 .{ext} 的解析器。")
