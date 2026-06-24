"""Local file ingestion service.

Converts uploaded files to Evidence objects using markitdown as the primary
parser (supports PDF, DOCX, XLSX, PPTX, HTML, images, audio, ZIP…).
Falls back to pypdf / python-docx / built-in str when markitdown is unavailable.
"""
from __future__ import annotations

import io
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlparse

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


def _to_evidence(text: str, filename: str, *, source: str = "local") -> list[Evidence]:
    """Split text into paragraph-level Evidence chunks."""
    stem = Path(filename).stem if "." in filename else filename[:80]
    chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) > 30]
    if not chunks and text.strip():
        chunks = [text.strip()[:2000]]
    if not chunks:
        return []
    evidence = []
    for i, chunk in enumerate(chunks[:40]):
        ident = f"{stem}#{i}" if source == "local" else f"{filename}#{i}"
        evidence.append(
            Evidence(
                source=source,
                identifier=ident,
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


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if host.lower() in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return False
    except ValueError:
        pass
    return True


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def ingest_url(url: str) -> list[Evidence]:
    """Fetch a web page and convert to Evidence chunks."""
    if not _is_safe_url(url):
        raise ValueError("URL must be a public http(s) address")
    import httpx

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": "FormuMind/0.1 (research platform)"})
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        body = resp.content

    if "html" in content_type or body.lstrip()[:15].lower().startswith(b"<!doctype") or b"<html" in body[:500].lower():
        text = _html_to_text(body.decode("utf-8", errors="replace"))
    else:
        text = _parse_text(body)

    if not text.strip():
        return [
            Evidence(
                source="web",
                identifier=url,
                title=url,
                snippet="无法从该 URL 提取文本",
                relevance=0.5,
            )
        ]

    chunks = _to_evidence(text, url, source="web")
    if chunks:
        chunks[0].identifier = url
        chunks[0].title = url
    return chunks


def ingest_text(text: str, title: str = "Pasted text") -> list[Evidence]:
    """Convert pasted plain text into Evidence chunks."""
    label = title.strip() or "Pasted text"
    chunks = _to_evidence(text, label, source="pasted")
    if chunks:
        chunks[0].title = label
        chunks[0].identifier = label
    elif text.strip():
        return [
            Evidence(
                source="pasted",
                identifier=label,
                title=label,
                snippet=text.strip()[:500],
                relevance=1.0,
            )
        ]
    return chunks


def ingest_files_batch(files: list[tuple[str, bytes]]) -> list[Evidence]:
    out: list[Evidence] = []
    for name, content in files:
        out.extend(ingest_file(name, content))
    return out
