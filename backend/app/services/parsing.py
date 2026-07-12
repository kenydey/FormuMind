"""Unified document parsing layer — every byte stream becomes Markdown here.

Single entry point (``parse_document``) used by file upload, URL ingestion and
the full-text fetcher, replacing the per-caller parser cascades.  Parsers are
pluggable and probed at call time:

* **PDF**: marker → MinerU → MarkItDown → pypdf, order controlled by
  ``FORMUMIND_PDF_PARSER`` (``auto`` tries best-first; naming a parser pins it
  with fallback to the tiers below it).  marker / MinerU produce real Markdown
  (layout-aware, tables preserved) and are optional heavy extras; MarkItDown
  is the light default; pypdf is the last-resort plain-text tier.
* **Other formats** (DOCX/XLSX/PPTX/HTML/…): MarkItDown → format-specific
  fallbacks (python-docx, plain text decode).

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


@dataclass
class ParseResult:
    markdown: str
    parser: str  # marker | mineru | markitdown | pypdf | docx | text | none

    @property
    def ok(self) -> bool:
        return bool(self.markdown.strip())


# ── individual parsers (return markdown/text or None) ────────────────────────

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
    """MinerU (magic-pdf): highest-fidelity PDF → Markdown (optional, GPU-friendly)."""
    try:
        import tempfile
        from pathlib import Path

        from magic_pdf.data.data_reader_writer import FileBasedDataWriter  # type: ignore
        from magic_pdf.data.dataset import PymuDocDataset  # type: ignore
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir:
            ds = PymuDocDataset(content)
            infer = doc_analyze(ds, ocr=False)
            writer = FileBasedDataWriter(tmpdir)
            result = infer.pipe_txt_mode(writer)
            md = result.get_markdown(str(Path(tmpdir)))
        return md or None
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "mineru parse failed")
        return None


def _parse_markitdown(content: bytes, ext: str) -> str | None:
    try:
        from markitdown import MarkItDown  # type: ignore

        result = MarkItDown().convert_stream(io.BytesIO(content), file_extension=ext)
        return result.text_content or None
    except ImportError:
        return None
    except Exception as exc:
        log_handled_exception(logger, exc, "markitdown parse failed")
        return None


def _parse_pypdf(content: bytes) -> str | None:
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(content))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return text if text.strip() else None
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

_PDF_TIERS: tuple[tuple[str, object], ...] = (
    ("marker", lambda c, e: _parse_marker(c)),
    ("mineru", lambda c, e: _parse_mineru(c)),
    ("markitdown", _parse_markitdown),
    ("pypdf", lambda c, e: _parse_pypdf(c)),
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

    text = _parse_markitdown(content, ext)
    if text and text.strip():
        return ParseResult(text, "markitdown")
    if ext in ("docx", "doc"):
        text = _parse_docx(content)
        if text:
            return ParseResult(text, "docx")
    if ext in ("txt", "md", "csv", "html", "htm", "json", "xml"):
        text = _parse_plain(content)
        if text and text.strip():
            return ParseResult(text, "text")
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
        "marker": optional_import("marker"),
        "mineru": optional_import("magic_pdf"),
        "markitdown": optional_import("markitdown"),
        "pypdf": optional_import("pypdf"),
        "trafilatura": optional_import("trafilatura"),
    }
