"""Patent full-text acquisition.

Everything goes through the **Google Patents landing page**, which is both the
index and — usefully — the full text:

    patents.google.com/patent/{id}/en
      ├─ <section itemprop="abstract|description|claims">  → full text, already
      │    plain (and machine-translated to English alongside the original for
      │    CN/JP/KR publications), no OCR needed
      └─ <meta name="citation_pdf_url">                    → the real PDF, on
           patentimages.storage.googleapis.com

HTML is preferred (``FORMUMIND_PATENT_PREFER_HTML``, default on): it is one
request instead of two, it never needs OCR — a scanned patent costs ~2 s/page
through RapidOCR and nothing through the landing page — and ``itemprop``
sectioning is cleaner than layout reconstruction from a PDF. The PDF tier is
kept for callers that want the original document (figures, chemical structure
drawings) and is the fallback when a publication has no HTML body.

Three older direct-PDF URLs were removed after measuring them against live
endpoints; each one cost a full timeout and returned nothing:

* ``pdfpiw.uspto.gov`` — connection reset by peer;
* ``patents.google.com/patent/{id}/pdf`` — HTTP 200 with ``text/html``
  (it is the landing page, not a PDF);
* ``data.epo.org/publication-server`` — HTTP 200 with a 2.4 KB error page,
  even for a valid EP publication number.

Gated by the config flag so tests run offline without network requests.
"""
from __future__ import annotations

import logging
from .errors import degrade_return, log_handled_exception
import re

import httpx

from ..domain.schemas import Evidence

logger = logging.getLogger(__name__)

# ── URL construction ─────────────────────────────────────────────────────────

_USPTO_NUM_RE = re.compile(r"US(\d+)", re.IGNORECASE)
_EPO_RE = re.compile(r"EP(\d+)([A-Z0-9]*)", re.IGNORECASE)

# Any two-letter office code plus digits — Google Patents serves CN/JP/WO/KR/DE
# as readily as US/EP, so the landing-page path is not office-specific.
_PUBNUM_RE = re.compile(r"^[A-Z]{2}[A-Z]?\d{3,}[A-Z0-9]*$")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FormuMind/0.9; patent-research) "
        "AppleWebKit/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
}

# The landing page is HTML; asking for PDF first makes some caches unhappy.
_HTML_HEADERS = {**_HEADERS, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}

# `citation_pdf_url` comes out of a third-party page, so it is untrusted input.
# Google serves every patent PDF from this one bucket; pinning the host means a
# tampered or redirected page cannot turn this into a request to somewhere else.
_PDF_HOST = "patentimages.storage.googleapis.com"

_CITATION_PDF_RE = re.compile(
    r"""<meta\s[^>]*name=["']citation_pdf_url["'][^>]*content=["']([^"']+)["']""",
    re.IGNORECASE,
)
# Attribute order is not guaranteed; accept content-before-name too.
_CITATION_PDF_ALT_RE = re.compile(
    r"""<meta\s[^>]*content=["']([^"']+)["'][^>]*name=["']citation_pdf_url["']""",
    re.IGNORECASE,
)

# Sections in the order they should read in the extracted Markdown. Claims last
# mirrors the printed document and keeps the abstract — the highest-signal
# paragraph — at the top where chunk 0 will pick it up.
_PATENT_SECTIONS = (("abstract", "Abstract"), ("description", "Description"), ("claims", "Claims"))


def _landing_url(patent_id: str) -> str:
    """Google Patents landing page for a publication number.

    ``/en`` asks for the English rendering, which is what makes a CN or JP
    publication readable: Google emits the machine translation *alongside* the
    original, so the extracted text carries both.
    """
    return f"https://patents.google.com/patent/{patent_id.strip().upper()}/en"


def _google_patents_url(patent_id: str) -> str:
    """Deprecated alias — the ``/pdf`` suffix serves HTML, not a PDF."""
    return _landing_url(patent_id)


# ── Landing page → text / PDF url ────────────────────────────────────────────


def _strip_tags(html: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    import html as html_mod

    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    # Block-level tags become newlines so paragraphs survive the strip.
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section)>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _section_text(html: str, itemprop: str) -> str:
    """Body of ``<section itemprop="...">``, tags stripped.

    Regex rather than a parser because these sections nest ``<div>``s but never
    another ``<section>``, so a non-greedy match to the first ``</section>``
    is exact — and it keeps this path dependency-free.
    """
    m = re.search(
        rf"""<section[^>]*itemprop=["']{itemprop}["'].*?</section>""", html, re.IGNORECASE | re.DOTALL
    )
    return _strip_tags(m.group(0)) if m else ""


def patent_text_from_html(html: str) -> str:
    """Landing page → Markdown with ``## Abstract / ## Description / ## Claims``.

    Returns "" when none of the sections are present (a not-found page, or a
    publication Google holds only as bibliographic data).
    """
    parts: list[str] = []
    for itemprop, heading in _PATENT_SECTIONS:
        body = _section_text(html, itemprop)
        # The section's own <h2> is inside the match, so drop a leading repeat
        # of the heading rather than printing it twice.
        body = re.sub(rf"^{heading}\s*(\(\s*\d+\s*\))?\s*", "", body, flags=re.IGNORECASE).strip()
        if len(body) > 40:
            parts.append(f"## {heading}\n\n{body}")
    return "\n\n".join(parts)


def _pdf_url_from_landing(html: str) -> str | None:
    """``citation_pdf_url`` from the landing page, host-pinned.

    A URL lifted out of a remote page is attacker-influenceable in principle;
    it is checked against the SSRF guard *and* the known bucket before use.
    """
    m = _CITATION_PDF_RE.search(html) or _CITATION_PDF_ALT_RE.search(html)
    if not m:
        return None
    url = m.group(1).strip()
    if url.startswith("//"):
        url = f"https:{url}"
    try:
        host = (httpx.URL(url).host or "").lower()
    except Exception as exc:
        return degrade_return(logger, exc, f"unparseable citation_pdf_url: {url[:120]}", None)
    if host != _PDF_HOST:
        logger.warning("citation_pdf_url host rejected (expected %s): %s", _PDF_HOST, host)
        return None
    from .ingestion import _is_safe_url

    if not _is_safe_url(url):
        logger.warning("citation_pdf_url blocked by SSRF guard: %s", url[:200])
        return None
    return url


def fetch_patent_landing(patent_id: str, timeout: float = 20.0) -> str | None:
    """GET the Google Patents landing page HTML, or None on failure."""
    url = _landing_url(patent_id)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTML_HEADERS) as client:
            r = client.get(url)
        if int(getattr(r, "status_code", 0)) != 200:
            return None
        return r.text or None
    except Exception as exc:
        return degrade_return(logger, exc, f"patent landing fetch failed: {url}", None)


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
    except Exception as exc:
        log_handled_exception(logger, exc, "handled exception")
    return None


def fetch_patent_pdf(patent_id: str, timeout: float = 20.0) -> bytes | None:
    """Resolve the real PDF via the landing page and download it.

    Two requests, both fast (~0.7 s + ~0.5 s measured), replacing three
    direct-URL guesses that each burned a timeout and returned nothing.
    """
    html = fetch_patent_landing(patent_id, timeout)
    if not html:
        return None
    pdf_url = _pdf_url_from_landing(html)
    if not pdf_url:
        # Normal, not an error: JP publications and some EP ones have no PDF on
        # Google Patents at all. The HTML body is the answer for those.
        logger.info("no citation_pdf_url for %s — HTML body is the only full text", patent_id)
        return None
    return fetch_pdf(pdf_url, timeout)


def fetch_patent_text(patent_id: str, timeout: float = 20.0, *, prefer_html: bool | None = None) -> str | None:
    """Full text for a publication number, from HTML or PDF.

    One landing-page request serves both tiers, so choosing HTML costs nothing
    extra and choosing PDF costs one more request rather than a fresh lookup.
    """
    from ..config import get_settings

    if prefer_html is None:
        prefer_html = bool(getattr(get_settings(), "patent_prefer_html", True))

    html = fetch_patent_landing(patent_id, timeout)
    if not html:
        return None

    if not prefer_html:
        pdf_url = _pdf_url_from_landing(html)
        if pdf_url:
            pdf = fetch_pdf(pdf_url, timeout)
            if pdf:
                text = _extract_text(pdf)
                if text and len(text.strip()) > 200:
                    return text
        # Fall through: a missing or unparseable PDF is not a reason to discard
        # a landing page that already holds the description and claims.

    text = patent_text_from_html(html)
    return text or None


# ── Text extraction ──────────────────────────────────────────────────────────


def _raw_pdf_stream_text(content: bytes) -> str:
    """Minimal fallback text extractor for simple unencrypted PDFs.

    Parses Tj (show string) operators directly from the content stream without
    any external library. Works for basic PDFs whose text is stored as plain
    Latin-1 strings — including the offline test fixture and most USPTO/EPO
    full-text PDFs. Does NOT handle FlateDecode compression or complex encodings.
    """
    import re
    import zlib

    texts: list[str] = []
    for raw_stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", content, re.DOTALL):
        stream = raw_stream
        try:
            stream = zlib.decompress(raw_stream)
        except Exception as exc:
            log_handled_exception(logger, exc, "handled exception")  # not FlateDecode or wrong boundaries — use raw bytes
        for m in re.finditer(rb"\(([^)]*)\)\s*Tj", stream):
            raw = m.group(1).replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            decoded = raw.decode("latin-1", errors="replace").strip()
            if decoded:
                texts.append(decoded)
    return "\n\n".join(texts)


def _extract_text(content: bytes) -> str:
    """Extract text/Markdown from PDF bytes.

    Delegates to the unified parsing layer (marker → MinerU → MarkItDown →
    pypdf, per FORMUMIND_PDF_PARSER), then falls back to the dependency-free
    raw Tj-operator parser that handles simple unencrypted PDFs (test fixture
    + most patent PDFs).
    """
    from .parsing import parse_document

    result = parse_document(content, "pdf")
    if result.ok:
        return result.markdown

    return _raw_pdf_stream_text(content)


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

    Called by DeepResearchEngine.run() when FORMUMIND_PDF_DOWNLOAD=true.
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
