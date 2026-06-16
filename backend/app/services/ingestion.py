"""Local file ingestion service.

Converts uploaded files to Evidence objects using markitdown as the primary
parser (supports PDF, DOCX, XLSX, PPTX, HTML, images, audio, ZIP…).
Falls back to pypdf / python-docx / built-in str when markitdown is unavailable.
"""
from __future__ import annotations

import io
from pathlib import Path

from ..domain.schemas import Evidence


def _parse_with_markitdown(content: bytes, ext: str) -> str | None:
    try:
        from markitdown import MarkItDown  # type: ignore
        md = MarkItDown()
        result = md.convert_stream(io.BytesIO(content), file_extension=ext)
        return result.text_content
    except Exception:
        return None


def _parse_pdf_fallback(content: bytes) -> str:
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(io.BytesIO(content))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _parse_docx_fallback(content: bytes) -> str:
    try:
        import docx  # type: ignore
        doc = docx.Document(io.BytesIO(content))
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


def _parse_text(content: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return content.decode(enc)
        except Exception:
            continue
    return ""


def _to_evidence(text: str, filename: str) -> list[Evidence]:
    """Split text into paragraph-level Evidence chunks."""
    stem = Path(filename).stem
    chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) > 30]
    if not chunks:
        return []
    evidence = []
    for i, chunk in enumerate(chunks[:40]):  # cap at 40 chunks per file
        evidence.append(
            Evidence(
                source="local",
                identifier=f"{stem}#{i}",
                title=stem if i == 0 else f"{stem} (p.{i+1})",
                snippet=chunk[:500],
                relevance=1.0 - i * 0.01,
            )
        )
    return evidence


def ingest_file(filename: str, content: bytes) -> list[Evidence]:
    """Parse an uploaded file and return a list of Evidence objects."""
    ext = Path(filename).suffix.lower().lstrip(".")

    # Try markitdown first (handles most formats)
    text = _parse_with_markitdown(content, ext)

    # Fallbacks for common formats
    if not text:
        if ext == "pdf":
            text = _parse_pdf_fallback(content)
        elif ext in ("docx", "doc"):
            text = _parse_docx_fallback(content)
        elif ext in ("txt", "md", "csv", "html", "htm"):
            text = _parse_text(content)

    if not text or not text.strip():
        return [Evidence(
            source="local",
            identifier=filename,
            title=filename,
            snippet=f"无法提取文本内容（格式：{ext}）",
            relevance=0.5,
        )]

    return _to_evidence(text, filename)
