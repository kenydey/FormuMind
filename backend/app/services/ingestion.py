"""Local file ingestion service.

Converts uploaded files to Evidence objects using markitdown as the primary
parser (supports PDF, DOCX, XLSX, PPTX, HTML, images, audio, ZIP…).
Falls back to pypdf / python-docx / built-in str when markitdown is unavailable.

Pipeline: parse → LLM source_guide → token-aware chunk → persist SourceDocument.
"""
from __future__ import annotations

import logging
from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import hashlib
import io
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..config import get_settings
from ..db.source_store import get_source_store
from ..domain.schemas import Evidence, SourceGuideSchema
from .source_guide import extract_source_guide

logger = logging.getLogger(__name__)


@dataclass
class IngestOutcome:
    evidence: list[Evidence]
    source_id: str | None = None
    source_guide: SourceGuideSchema | None = None
    extraction_status: str = "skipped"


def _parse_with_markitdown(content: bytes, ext: str) -> str | None:
    try:
        from markitdown import MarkItDown  # type: ignore
        md = MarkItDown()
        result = md.convert_stream(io.BytesIO(content), file_extension=ext)
        return result.text_content
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


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


def _chunk_text(
    text: str,
    *,
    max_chars: int = 1600,
    overlap: int = 200,
    max_depth: int = 10,
    _depth: int = 0,
) -> list[str]:
    """Recursive split on \\n\\n > \\n > 句号，控制 chunk 大小。"""
    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    if _depth >= max_depth:
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
        return chunks

    for sep in ("\n\n", "\n", "。", ". "):
        if sep not in text:
            continue
        parts = text.split(sep)
        chunks = []
        current = ""
        for i, part in enumerate(parts):
            piece = part if i == len(parts) - 1 else part + sep
            if len(current) + len(piece) <= max_chars:
                current += piece
            else:
                if current.strip():
                    chunks.append(current.strip())
                if len(piece) > max_chars:
                    chunks.extend(
                        _chunk_text(
                            piece,
                            max_chars=max_chars,
                            overlap=overlap,
                            max_depth=max_depth,
                            _depth=_depth + 1,
                        )
                    )
                    current = ""
                else:
                    current = piece
        if current.strip():
            chunks.append(current.strip())
        if chunks:
            return chunks

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _to_evidence(text: str, filename: str, *, source: str = "local") -> list[Evidence]:
    """Split text into chunk-level Evidence objects."""
    settings = get_settings()
    stem = Path(filename).stem if "." in filename else filename[:80]
    chunks = _chunk_text(
        text,
        max_chars=settings.ingest_chunk_max_chars,
        overlap=settings.ingest_chunk_overlap,
    )
    chunks = [c for c in chunks if len(c.strip()) > 30]
    if not chunks and text.strip():
        chunks = [text.strip()[:2000]]
    if not chunks:
        return []

    max_chunks = settings.ingest_max_chunks
    evidence: list[Evidence] = []
    for i, chunk in enumerate(chunks[:max_chunks]):
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


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _ingest_parsed_text(
    text: str,
    *,
    filename: str,
    source_kind: str,
    persist: bool = True,
) -> IngestOutcome:
    settings = get_settings()
    guide: SourceGuideSchema | None = None
    err: str | None = None
    status = "skipped"

    if settings.source_guide_enabled and text.strip() and settings.get_active_api_key():
        guide, err = extract_source_guide(text, title=filename)
        if guide and guide.status == "verified":
            status = "ok"
        elif guide:
            status = "degraded"
        else:
            status = "failed"
    elif settings.source_guide_enabled and text.strip() and not settings.get_active_api_key():
        status = "skipped"

    evidence = _to_evidence(text, filename, source=source_kind)

    source_id: str | None = None
    if persist and text.strip():
        source_id = get_source_store().create(
            filename=filename,
            title=Path(filename).stem if "." in filename else filename[:80],
            source_kind=source_kind,
            full_text=text,
            content_hash=_content_hash(text),
            source_guide=guide,
            extraction_status=status,
            extraction_error=err,
        )

    return IngestOutcome(evidence, source_id, guide, status)


def ingest_file(filename: str, content: bytes, *, persist: bool = True) -> IngestOutcome:
    """Parse an uploaded file and return ingest outcome."""
    ext = Path(filename).suffix.lower().lstrip(".")

    text = _parse_with_markitdown(content, ext)

    if not text:
        if ext == "pdf":
            text = _parse_pdf_fallback(content)
        elif ext in ("docx", "doc"):
            text = _parse_docx_fallback(content)
        elif ext in ("txt", "md", "csv", "html", "htm"):
            text = _parse_text(content)

    if not text or not text.strip():
        placeholder = Evidence(
            source="local",
            identifier=filename,
            title=filename,
            snippet=f"无法提取文本内容（格式：{ext}）",
            relevance=0.5,
        )
        return IngestOutcome(evidence=[placeholder], extraction_status="skipped")

    return _ingest_parsed_text(text, filename=filename, source_kind="local", persist=persist)


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _host_resolves_to_blocked(host: str) -> bool:
    import socket

    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
        except socket.gaierror:
            continue
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if _is_blocked_ip(addr):
                return True
    return False


def _is_safe_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    host = _normalize_host(host)
    if host in ("localhost", "localhost.localdomain", "0.0.0.0"):
        return False
    if host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        if _is_blocked_ip(ipaddress.ip_address(host)):
            return False
    except ValueError:
        pass
    if _host_resolves_to_blocked(host):
        return False
    return True


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def ingest_url(url: str, *, persist: bool = True) -> IngestOutcome:
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
        return IngestOutcome(
            evidence=[
                Evidence(
                    source="web",
                    identifier=url,
                    title=url,
                    snippet="无法从该 URL 提取文本",
                    relevance=0.5,
                )
            ],
            extraction_status="skipped",
        )

    outcome = _ingest_parsed_text(text, filename=url, source_kind="web", persist=persist)
    if outcome.evidence:
        outcome.evidence[0].identifier = url
        outcome.evidence[0].title = url
    return outcome


def ingest_text(text: str, title: str = "Pasted text", *, persist: bool = True) -> IngestOutcome:
    """Convert pasted plain text into Evidence chunks."""
    label = title.strip() or "Pasted text"
    if not text.strip():
        return IngestOutcome(evidence=[], extraction_status="skipped")

    outcome = _ingest_parsed_text(text, filename=label, source_kind="pasted", persist=persist)
    if outcome.evidence:
        outcome.evidence[0].title = label
        outcome.evidence[0].identifier = label
    elif text.strip():
        return IngestOutcome(
            evidence=[
                Evidence(
                    source="pasted",
                    identifier=label,
                    title=label,
                    snippet=text.strip()[:500],
                    relevance=1.0,
                )
            ],
            extraction_status=outcome.extraction_status,
            source_id=outcome.source_id,
            source_guide=outcome.source_guide,
        )
    return outcome


def ingest_files_batch(files: list[tuple[str, bytes]], *, persist: bool = True) -> IngestOutcome:
    all_evidence: list[Evidence] = []
    last_outcome: IngestOutcome | None = None
    for name, content in files:
        outcome = ingest_file(name, content, persist=persist)
        all_evidence.extend(outcome.evidence)
        last_outcome = outcome
    return IngestOutcome(
        evidence=all_evidence,
        source_id=last_outcome.source_id if last_outcome else None,
        source_guide=last_outcome.source_guide if last_outcome else None,
        extraction_status=last_outcome.extraction_status if last_outcome else "skipped",
    )
