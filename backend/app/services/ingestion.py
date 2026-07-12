"""Local file ingestion service.

Converts uploaded files to Evidence objects via the unified parsing layer
(``services.parsing``: marker/MinerU/MarkItDown/pypdf cascade for PDFs,
MarkItDown + format fallbacks for everything else) and structure-aware
chunking (``services.chunking``: heading paths preserved, tables atomic).

Pipeline: parse → LLM source_guide → structure-aware chunk → persist SourceDocument.
"""
from __future__ import annotations

import logging
import hashlib
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


def _parse_text(content: bytes) -> str:
    from .parsing import _parse_plain

    return _parse_plain(content) or ""


def _chunk_text(
    text: str, *, max_chars: int = 1600, overlap: int = 200, max_depth: int = 10
) -> list[str]:
    """Backward-compatible alias for the plain-text splitter (see chunking.py)."""
    from .chunking import chunk_plain_text

    return chunk_plain_text(text, max_chars=max_chars, overlap=overlap, max_depth=max_depth)


def _to_evidence(text: str, filename: str, *, source: str = "local") -> list[Evidence]:
    """Split text into chunk-level Evidence objects (structure-aware).

    Markdown heading paths are appended to chunk titles (``report (p.3) ·
    实施例 > 实施例 2``) so TF-IDF / ColBERT retrieval and citations see the
    document location; tables stay atomic.
    """
    from .chunking import chunk_markdown

    settings = get_settings()
    stem = Path(filename).stem if "." in filename else filename[:80]
    chunks = chunk_markdown(
        text,
        max_chars=settings.ingest_chunk_max_chars,
        overlap=settings.ingest_chunk_overlap,
    )
    chunks = [c for c in chunks if len(c.text.strip()) > 30]
    if not chunks and text.strip():
        from .chunking import Chunk

        chunks = [Chunk(text.strip()[:2000])]
    if not chunks:
        return []

    max_chunks = settings.ingest_max_chunks
    evidence: list[Evidence] = []
    for i, chunk in enumerate(chunks[:max_chunks]):
        ident = f"{stem}#{i}" if source == "local" else f"{filename}#{i}"
        title = stem if i == 0 else f"{stem} (p.{i+1})"
        if chunk.heading_path:
            title = f"{title} · {chunk.heading_path}"
        evidence.append(
            Evidence(
                source=source,
                identifier=ident,
                title=title,
                snippet=chunk.text[:500],
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
    from .parsing import parse_document

    ext = Path(filename).suffix.lower().lstrip(".")
    text = parse_document(content, ext).markdown

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
    """HTML → Markdown/text via the unified parsing layer (trafilatura first)."""
    from .parsing import html_to_markdown

    return html_to_markdown(html)


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
