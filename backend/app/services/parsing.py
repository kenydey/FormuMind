"""Unified document parsing layer — every byte stream becomes Markdown here.

Single entry point (``parse_document``) used by file upload, URL ingestion and
the full-text fetcher, replacing the per-caller parser cascades.  Parsers are
pluggable and probed at call time:

* **PDF**: Docling → marker → MinerU → MarkItDown → pypdf, order controlled by
  ``FORMUMIND_PDF_PARSER`` (``auto`` tries best-first; naming a parser pins it
  with fallback to the tiers below it).  Docling / marker / MinerU produce
  real Markdown (layout-aware, tables preserved; Docling and MinerU can emit
  equations as LaTeX) and are optional heavy extras; MarkItDown is the light
  default; pypdf is the last-resort plain-text tier.
* **Other formats** (DOCX/XLSX/PPTX/HTML/…): MarkItDown → format-specific
  fallbacks (python-docx, plain text decode).
* **Page provenance**: Docling and pypdf interleave ``<!-- page:N -->``
  markers; the chunker consumes them into ``Chunk.page_no`` and strips them.

Every parser is optional; the layer degrades tier by tier and reports which
parser produced the output so provenance can be persisted.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

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

    @property
    def ok(self) -> bool:
        return bool(self.markdown.strip())


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


def _parse_mineru(content: bytes) -> str | None:
    """MinerU (magic-pdf): highest-fidelity PDF → Markdown (optional, GPU-friendly).

    Chemistry-aware knobs: ``FORMUMIND_PDF_OCR`` routes scanned documents
    through the OCR pipeline; formula/table recognition is requested when the
    installed magic-pdf supports it (equations come back as LaTeX).
    """
    try:
        import tempfile
        from pathlib import Path

        from magic_pdf.data.data_reader_writer import FileBasedDataWriter  # type: ignore
        from magic_pdf.data.dataset import PymuDocDataset  # type: ignore
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze  # type: ignore

        ocr = bool(get_settings().pdf_ocr)
        with tempfile.TemporaryDirectory() as tmpdir:
            ds = PymuDocDataset(content)
            try:
                infer = doc_analyze(ds, ocr=ocr, formula_enable=True, table_enable=True)
            except TypeError:  # older magic-pdf without the enable kwargs
                infer = doc_analyze(ds, ocr=ocr)
            writer = FileBasedDataWriter(tmpdir)
            pipe = getattr(infer, "pipe_ocr_mode", None) if ocr else None
            result = pipe(writer) if pipe else infer.pipe_txt_mode(writer)
            md = result.get_markdown(str(Path(tmpdir)))
        return md or None
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "mineru parse failed")
        return None


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


def _parse_plain(content: bytes) -> str | None:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return None


# ── registry ─────────────────────────────────────────────────────────────────

# Every entry wraps its parser in a lambda so the name resolves at call time.
# markitdown used to be held by direct reference, which made it the one tier a
# test could not monkeypatch — the patch was accepted and silently ignored.
_PDF_TIERS: tuple[tuple[str, object], ...] = (
    ("docling", lambda c, e: _parse_docling(c)),
    ("marker", lambda c, e: _parse_marker(c)),
    ("mineru", lambda c, e: _parse_mineru(c)),
    ("markitdown", lambda c, e: _parse_markitdown(c, e)),
    ("pypdf", lambda c, e: _parse_pypdf(c)),
)


# Non-PDF tiers, in the same shape as _PDF_TIERS. This used to be a hardcoded
# if-chain, which meant every new fallback had to be wedged into the control
# flow rather than registered alongside its peers.
_DOC_TIERS: tuple[tuple[str, object], ...] = (
    ("markitdown", lambda c, e: _parse_markitdown(c, e)),
    ("docx", lambda c, e: _parse_docx(c) if e in ("docx", "doc") else None),
    ("text", lambda c, e: _parse_plain(c) if e in _ALWAYS_PARSEABLE else None),
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
    """Parse *content* (with file extension *ext*, no dot) into Markdown/text."""
    ext = (ext or "").lower().lstrip(".")
    if not content:
        return ParseResult("", "none")

    if ext == "pdf":
        order = _pdf_tier_order(prefer if prefer is not None else get_settings().pdf_parser)
        for name, fn in order:
            text = fn(content, ext)
            if text and text.strip():
                return ParseResult(text, name)
        return ParseResult("", "none")

    for name, fn in _DOC_TIERS:
        text = fn(content, ext)
        if text and text.strip():
            return ParseResult(text, name)
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
        "docling": optional_import("docling"),
        "marker": optional_import("marker"),
        "mineru": optional_import("magic_pdf"),
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
                optional_import("docling"),
                optional_import("marker"),
                optional_import("magic_pdf"),
                _markitdown_can("pdf"),
                optional_import("pypdf"),
            )
        ),
        "docx": _markitdown_can("docx") or optional_import("docx"),
        "pptx": _markitdown_can("pptx"),
        "xlsx": _markitdown_can("xlsx"),
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
