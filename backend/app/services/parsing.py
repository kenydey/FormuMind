"""Unified document parsing layer — every byte stream becomes Markdown here.

Single entry point (``parse_document``) used by file upload, URL ingestion and
the full-text fetcher, replacing the per-caller parser cascades.  Parsers are
pluggable and probed at call time:

* **PDF**: hybrid (pymupdf4llm + 云端 MinerU 按页升级 + 本地 OCR，CPU-cheap 主路径)
  → docling → marker → mineru(magic-pdf 本地) → rapidocr → markitdown → pypdf,
  order controlled by ``FORMUMIND_PDF_PARSER`` (``auto`` tries best-first; naming
  a parser pins it with fallback to the tiers below it).  hybrid 已内置本地布局
  解析 + 本地 OCR + 云端 MinerU，后面的 docling / marker / 本地 magic-pdf 是
  离线高保真降级（docling/marker 需 weights、CPU 极慢，本地 magic-pdf 需 GPU）；
  markitdown / pypdf 是纯文本兜底。
* **Other formats** (DOCX/XLSX/PPTX/HTML/…): MarkItDown → format-specific
  fallbacks (python-docx, plain text decode).
* **Page provenance**: Docling and pypdf interleave ``<!-- page:N -->``
  markers; the chunker consumes them into ``Chunk.page_no`` and strips them.

Every parser is optional; the layer degrades tier by tier and reports which
parser produced the output so provenance can be persisted.
"""
from __future__ import annotations

import hashlib
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
    parser: str  # docling | marker | mineru | markitdown | pypdf | docx | text | none
    # W2-3: structured table assets extracted after a successful parse
    # (table_contract.TableAsset). Empty when extraction is disabled/failed.
    tables: list = field(default_factory=list)
    # Phase 1: layout-aware structured products (MinerUStructured) from the
    # mineru tier — blocks with page/kind, tables, formulas. None for the
    # text chain. Consumed by ingestion → extraction_tables/formulas.
    structured: object = None

    @property
    def ok(self) -> bool:
        return bool(self.markdown.strip())


def _maybe_extract_tables(result: ParseResult, content: bytes) -> ParseResult:
    """W2-3 wiring: table-contract extraction on the successful parse path.

    Fail-open by contract: any error leaves ``result`` untouched (tables=[]).
    The sidecar key is the content sha256, which equals
    ``SourceDocument.content_hash``, so assets rejoin documents without any
    DB schema change. Gate: ``table_extract_enabled`` (default True).

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
        from . import table_normalize as _tn
        blocks = _tc.blocks_from_markdown(result.markdown or "")
        if not blocks:
            return result
        source_id = hashlib.sha256(content).hexdigest()
        assets = _tc.extract_tables(source_id, blocks, parser=result.parser)
        result.tables = assets
        if assets:
            # W3-1: normalise tables → PropertySets, persisted in the same
            # sidecar JSON. Fail-open: normalisation never blocks extraction.
            try:
                prop_dicts = [p.to_dict() for p in _tn.normalize_tables(assets)]
            except Exception:
                logger.exception("table_normalize: failed (fail-open)")
                prop_dicts = None
            _tc.save_tables(source_id, assets, property_sets=prop_dicts)
    except Exception:
        logger.exception("table_contract: extraction failed (fail-open)")
    return result


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


def _parse_docx(content: bytes) -> str | None:
    try:
        import docx  # type: ignore

        doc = docx.Document(io.BytesIO(content))
        text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        return text if text.strip() else None
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "docx parse failed")
        return None


def _parse_xlsx(content: bytes) -> str | None:
    """Lightweight openpyxl fallback when markitdown[xlsx] is not installed."""
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets:
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


def _parse_plain(content: bytes) -> str | None:
    for enc in ("utf-8", "gbk", "latin-1"):
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
    ("text", lambda c, e: _parse_plain(c) if e in _ALWAYS_PARSEABLE else None),
    ("markitdown", lambda c, e: _parse_markitdown(c, e)),
    ("docx", lambda c, e: _parse_docx(c) if e in ("docx", "doc") else None),
    ("xlsx", lambda c, e: _parse_xlsx(c) if e in ("xlsx", "xlsm") else None),
)


def _pdf_tier_order(prefer: str) -> list[tuple[str, object]]:
    tiers = list(_PDF_TIERS)
    if prefer in ("auto", ""):
        return tiers
    names = [n for n, _ in tiers]
    if prefer not in names:
        logger.warning("unknown FORMUMIND_PDF_PARSER=%r — using auto order", prefer)
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

    with timing.span("parse"):
        if ext == "pdf":
            order = _pdf_tier_order(prefer if prefer is not None else get_settings().pdf_parser)
            for name, fn in order:
                out = fn(content, ext)
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
            text = fn(content, ext)
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
            return text
    except ImportError:
        pass
    except Exception as exc:
        log_handled_exception(logger, exc, "trafilatura extract failed")

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    return re.sub(r"[ \t]+", " ", text).strip()


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
        "rapidocr": optional_import("rapidocr_onnxruntime"),
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
                optional_import("magic_pdf"),
                # Local OCR reads scans, which none of the text-layer parsers can.
                optional_import("rapidocr_onnxruntime"),
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
