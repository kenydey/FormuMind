"""Patent PDF full-text downloader.

Downloads PDFs from public patent offices (USPTO, EPO) and parses them into
chunked Evidence items using markitdown (preferred) or pypdf (fallback).
Integrates with KnowledgeCohort to enrich abstract snippets with full text
when ``FORMUMIND_PDF_DOWNLOAD=true`` is set.

Gated by the config flag so tests run offline without network requests.
"""
from __future__ import annotations

import re

import httpx

from ..domain.schemas import Evidence

# ── URL construction ─────────────────────────────────────────────────────────

_USPTO_NUM_RE = re.compile(r"US(\d+)", re.IGNORECASE)
_EPO_RE = re.compile(r"EP(\d+)([A-Z0-9]*)", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FormuMind/0.9; patent-research) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
}


def _patent_pdf_url(patent_id: str) -> str | None:
    """Construct a PDF download URL for a known patent identifier."""
    pid = patent_id.strip().upper()
    m = _USPTO_NUM_RE.match(pid)
    if m:
        num = m.group(1).lstrip("0") or "0"
        return f"https://pdfpiw.uspto.gov/.pdf?Docid={num.zfill(8)}"
    m = _EPO_RE.match(pid)
    if m:
        num, kind = m.group(1), (m.group(2) or "A1")
        return f"https://data.epo.org/publication-server/pdf-document?CC=EP&NR={num}&KD={kind}"
    return None


def _google_patents_url(patent_id: str) -> str:
    return f"https://patents.google.com/patent/{patent_id}/pdf"


# ── Download ─────────────────────────────────────────────────────────────────


def fetch_pdf(url: str, timeout: float = 20.0) -> bytes | None:
    """GET *url*, return PDF bytes or None on any failure."""
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        ) as client:
            r = client.get(url)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "pdf" in ct.lower():
            return r.content
    except Exception:
        pass
    return None


def fetch_patent_pdf(patent_id: str, timeout: float = 20.0) -> bytes | None:
    """Try primary office URL then Google Patents fallback; return bytes or None."""
    primary = _patent_pdf_url(patent_id)
    if primary:
        pdf = fetch_pdf(primary, timeout)
        if pdf:
            return pdf
    return fetch_pdf(_google_patents_url(patent_id), timeout)


# ── Text extraction ──────────────────────────────────────────────────────────


def _extract_text(content: bytes) -> str:
    """Extract plain text from PDF bytes via markitdown → pypdf → empty string."""
    from io import BytesIO

    try:
        from markitdown import MarkItDown  # type: ignore

        result = MarkItDown().convert_stream(BytesIO(content), file_extension=".pdf")
        text = getattr(result, "text_content", "") or ""
        if text.strip():
            return text
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(content))
        return "\n\n".join(
            (page.extract_text() or "").strip() for page in reader.pages
        )
    except Exception:
        pass

    return ""


# ── Chunking ─────────────────────────────────────────────────────────────────


def pdf_to_evidence(
    content: bytes,
    source: str,
    identifier: str,
    title: str,
    base_relevance: float = 1.0,
    max_chunks: int = 6,
    min_chunk_len: int = 100,
) -> list[Evidence]:
    """Parse PDF bytes into chunked Evidence items (one per meaningful paragraph)."""
    text = _extract_text(content)
    if not text:
        return []

    paras = [
        p.strip()
        for p in re.split(r"\n{2,}", text)
        if len(p.strip()) >= min_chunk_len
    ]
    return [
        Evidence(
            source=source,
            identifier=f"{identifier}#p{i}",
            title=title,
            snippet=para[:600],
            relevance=round(max(0.2, base_relevance - i * 0.05), 3),
        )
        for i, para in enumerate(paras[:max_chunks])
    ]


# ── Public enrichment API ─────────────────────────────────────────────────────


def enrich_with_fulltext(
    evidence: list[Evidence],
    max_pdfs: int = 3,
    timeout: float = 20.0,
) -> list[Evidence]:
    """Replace abstract-only Evidence with full-text PDF chunks where possible.

    For each of the top ``max_pdfs`` patent Evidence items, attempts a PDF
    download; on success the original item is replaced by its paragraph chunks.
    Items without a downloadable PDF (no identifier, download failed, or
    max_pdfs exceeded) are returned unchanged.

    Called by KnowledgeCohort.run() when FORMUMIND_PDF_DOWNLOAD=true.
    """
    enriched: list[Evidence] = []
    downloaded = 0

    for ev in evidence:
        if downloaded >= max_pdfs or not ev.identifier:
            enriched.append(ev)
            continue

        # Only attempt downloads for identifiers that look like patent numbers.
        if not (_USPTO_NUM_RE.match(ev.identifier.upper()) or _EPO_RE.match(ev.identifier.upper())):
            enriched.append(ev)
            continue

        pdf = fetch_patent_pdf(ev.identifier, timeout=timeout)
        if pdf:
            chunks = pdf_to_evidence(
                pdf,
                source=ev.source,
                identifier=ev.identifier,
                title=ev.title,
                base_relevance=ev.relevance,
            )
            if chunks:
                enriched.extend(chunks)
                downloaded += 1
                continue

        enriched.append(ev)

    return enriched
