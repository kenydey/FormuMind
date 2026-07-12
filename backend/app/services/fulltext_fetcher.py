"""Full-text acquisition layer — upgrades abstract-level search hits to
full-document chunks and persists the raw text into the source store.

Fetcher registry by evidence kind (each fetcher returns plain text or None):

* **patent**   — USPTO / EPO / Google Patents PDF (reuses ``pdf_downloader``);
* **literature** — Open Access PDF located via OpenAlex (DOI) or arXiv id;
* **web**      — page body via trafilatura (preferred, boilerplate-free
  Markdown) with an HTML-strip fallback; SSRF-guarded.

Gated by ``FORMUMIND_FULLTEXT_ENRICH`` (default off so tests stay offline).
On success the original one-liner Evidence is replaced *in position* by
chunk-level Evidence rows, and the full text is persisted as a
``SourceDocument`` (dedup by content hash) so later phases can re-chunk or
re-index without re-downloading.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import re
from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from ..domain.schemas import Evidence
from .errors import degrade_return

logger = logging.getLogger(__name__)

_PATENT_RE = re.compile(r"^(US|EP)\d+", re.IGNORECASE)
_DOI_RE = re.compile(r"(?:doi:)?\s*(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)", re.IGNORECASE)
_ARXIV_RE = re.compile(r"(?:arxiv[:/]|abs/)(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)

_HEADERS = {"User-Agent": "FormuMind/1.0 (research platform; full-text fetcher)"}


@dataclass
class FulltextReport:
    attempted: int = 0
    succeeded: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    def record(self, kind: str, ok: bool) -> None:
        self.attempted += 1
        if ok:
            self.succeeded += 1
            self.by_kind[kind] = self.by_kind.get(kind, 0) + 1

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "by_kind": dict(self.by_kind),
        }


# ── kind classification ──────────────────────────────────────────────────────


def classify(ev: Evidence) -> str | None:
    """Return the fetcher kind for an Evidence row, or None when un-fetchable."""
    ident = (ev.identifier or "").strip()
    if not ident or ev.is_seed_corpus or re.search(r"#p?\d+$", ident):
        return None  # already chunk-level or synthetic
    if _PATENT_RE.match(ident.upper()):
        return "patent"
    if _DOI_RE.search(ident) or _ARXIV_RE.search(ident):
        return "literature"
    if ident.lower().startswith(("http://", "https://")):
        if _ARXIV_RE.search(ident):
            return "literature"
        return "web"
    return None


# ── fetchers (text or None) ──────────────────────────────────────────────────


def _fetch_patent_text(ev: Evidence, timeout: float) -> str | None:
    from .pdf_downloader import _extract_text, fetch_patent_pdf

    pdf = fetch_patent_pdf(ev.identifier.strip().upper(), timeout=timeout)
    if not pdf:
        return None
    text = _extract_text(pdf)
    return text if text and len(text.strip()) > 200 else None


def _resolve_oa_pdf_url(ev: Evidence, timeout: float) -> str | None:
    """Locate an Open Access PDF for a DOI (OpenAlex) or arXiv id."""
    ident = ev.identifier or ""
    m = _ARXIV_RE.search(ident)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    m = _DOI_RE.search(ident)
    if not m:
        return None
    doi = m.group(1).rstrip(".,;)")
    settings = get_settings()
    mailto = settings.openalex_mailto or "formumind@example.com"
    url = f"https://api.openalex.org/works/doi:{doi}?mailto={mailto}"
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS) as client:
            r = client.get(url)
            if r.status_code != 200:
                return None
            data = r.json()
        loc = data.get("best_oa_location") or {}
        return loc.get("pdf_url") or None
    except Exception as exc:
        return degrade_return(logger, exc, "OpenAlex OA resolution failed", None)


def _fetch_literature_text(ev: Evidence, timeout: float) -> str | None:
    from .pdf_downloader import _extract_text, fetch_pdf

    pdf_url = _resolve_oa_pdf_url(ev, timeout)
    if not pdf_url:
        return None
    pdf = fetch_pdf(pdf_url, timeout=timeout)
    if not pdf:
        return None
    text = _extract_text(pdf)
    return text if text and len(text.strip()) > 200 else None


def _extract_web_text(html: str) -> str:
    """Web body → Markdown via the unified parsing layer (trafilatura first)."""
    from .parsing import html_to_markdown

    return html_to_markdown(html)


def _fetch_web_text(ev: Evidence, timeout: float) -> str | None:
    from .ingestion import _is_safe_url

    url = (ev.identifier or "").strip()
    if not _is_safe_url(url):
        return None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HEADERS) as client:
            r = client.get(url)
            if r.status_code != 200:
                return None
            ctype = (r.headers.get("content-type") or "").lower()
            if "pdf" in ctype:
                from .pdf_downloader import _extract_text

                text = _extract_text(r.content)
            else:
                text = _extract_web_text(r.text)
    except Exception as exc:
        return degrade_return(logger, exc, f"web fulltext fetch failed: {url}", None)
    return text if text and len(text.strip()) > 200 else None


def _dispatch_fetch(kind: str, ev: Evidence, timeout: float) -> str | None:
    """Resolve the fetcher at call time (keeps the registry monkeypatchable)."""
    if kind == "patent":
        return _fetch_patent_text(ev, timeout)
    if kind == "literature":
        return _fetch_literature_text(ev, timeout)
    if kind == "web":
        return _fetch_web_text(ev, timeout)
    return None


# ── chunking + persistence ───────────────────────────────────────────────────


def _text_to_chunks(text: str, ev: Evidence) -> list[Evidence]:
    """Split fetched full text into chunk Evidence rows preserving provenance."""
    from .ingestion import _chunk_text

    settings = get_settings()
    chunks = _chunk_text(
        text,
        max_chars=settings.ingest_chunk_max_chars,
        overlap=settings.ingest_chunk_overlap,
    )
    chunks = [c for c in chunks if len(c.strip()) > 30][: settings.ingest_max_chunks]
    return [
        Evidence(
            source=ev.source,
            identifier=f"{ev.identifier}#p{i}",
            title=ev.title if i == 0 else f"{ev.title} (p.{i + 1})",
            snippet=chunk[:600],
            relevance=max(0.2, round(ev.relevance - i * 0.01, 3)),
        )
        for i, chunk in enumerate(chunks)
    ]


def _persist_fulltext(text: str, ev: Evidence, kind: str) -> str | None:
    """Store the raw full text as a SourceDocument (dedup by content hash)."""
    from ..db.source_store import get_source_store

    try:
        store = get_source_store()
        content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        existing = store.find_by_hash(content_hash)
        if existing is not None:
            return existing.id
        source_id = store.create(
            filename=ev.identifier[:500],
            title=ev.title[:500],
            source_kind=kind,
            full_text=text,
            content_hash=content_hash,
            extraction_status="fulltext",
        )
        from .kb_index import index_source

        index_source(source_id, text)
        return source_id
    except Exception as exc:
        return degrade_return(logger, exc, "fulltext persistence failed", None)


# ── public API ───────────────────────────────────────────────────────────────


def enrich_search_results(
    evidence: list[Evidence],
    *,
    max_docs: int | None = None,
    persist: bool = True,
) -> tuple[list[Evidence], FulltextReport]:
    """Replace the top fetchable Evidence rows with full-text chunks in place.

    Order is preserved; rows that fail to fetch (or beyond ``max_docs``) pass
    through unchanged.  Strict no-op when ``fulltext_enrich`` is disabled.
    """
    settings = get_settings()
    report = FulltextReport()
    if not settings.fulltext_enrich or not evidence:
        return evidence, report

    limit = max_docs if max_docs is not None else settings.fulltext_max_docs
    timeout = float(settings.fulltext_timeout_s)

    # Pick the first `limit` fetchable rows in rank order.
    targets: dict[int, str] = {}
    for i, ev in enumerate(evidence):
        if len(targets) >= limit:
            break
        kind = classify(ev)
        if kind:
            targets[i] = kind
    if not targets:
        return evidence, report

    def fetch(idx: int) -> tuple[int, str | None]:
        ev, kind = evidence[idx], targets[idx]
        try:
            return idx, _dispatch_fetch(kind, ev, timeout)
        except Exception as exc:
            return idx, degrade_return(logger, exc, f"fulltext fetch failed ({kind})", None)

    results: dict[int, str | None] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for idx, text in ex.map(fetch, list(targets)):
            results[idx] = text

    out: list[Evidence] = []
    for i, ev in enumerate(evidence):
        kind = targets.get(i)
        text = results.get(i)
        if kind and text:
            chunks = _text_to_chunks(text, ev)
            if chunks:
                if persist:
                    _persist_fulltext(text, ev, kind)
                out.extend(chunks)
                report.record(kind, True)
                continue
        if kind:
            report.record(kind, False)
        out.append(ev)

    logger.info(
        "fulltext_fetcher: %d/%d succeeded %s",
        report.succeeded,
        report.attempted,
        report.by_kind,
    )
    return out, report
