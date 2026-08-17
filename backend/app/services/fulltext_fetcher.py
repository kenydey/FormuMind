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
from . import ingest_timing as _timing
from .errors import degrade_return

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Full-text acquisition failed with a specific, user-facing reason.

    Raised by the fetchers so the ingest state machine can record *why* a
    document was unobtainable — "无 OA 版本" vs "下载超时" vs "解析为空" —
    instead of collapsing all three into one opaque message.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

# Any patent office, not just US/EP. `fetch_patent_pdf` falls back to Google
# Patents, which serves CN/JP/WO/KR/DE publications too — so restricting this to
# US|EP did not reflect what could be fetched, it just declined to try. On a
# Chinese-language corpus that silently dropped most patent hits from the ingest
# queue entirely: searched, listed in the UI, never stored.
#
# Two letters plus at least four digits is the standard publication-number shape
# (CN102345678A, WO2020123456A1, JP2001234567A). The trailing kind code is
# optional and not matched, since it does not affect fetchability.
#
# The optional third letter is the Japanese era marker: pre-2000 JP numbers are
# written JPH… (Heisei) or JPS… (Showa). Without it, JPH0925455A did not
# classify as a patent at all — searched, shown in the UI, never queued for
# ingest. Found by running a real mixed batch, which is also how the CN case
# surfaced; a shape nobody happened to test simply disappears.
_PATENT_RE = re.compile(r"^[A-Z]{2}[A-Z]?\d{4,}", re.IGNORECASE)
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
    """Full text via the Google Patents landing page (HTML body or its PDF).

    See ``pdf_downloader`` for why this is one lookup and not three direct-URL
    guesses: all three of those endpoints are dead, so every patent used to
    burn two timeouts and yield nothing.
    """
    from .pdf_downloader import fetch_patent_text

    text = fetch_patent_text(ev.identifier.strip().upper(), timeout=timeout)
    return text if text and len(text.strip()) > 200 else None


def _resolve_oa_pdf_url(ev: Evidence, timeout: float) -> str | None:
    """Locate an Open Access PDF for a DOI (OpenAlex) or arXiv id.

    Prefers OA metadata carried from the search tier (``ev.oa_pdf_url``) so a
    non-OA DOI never triggers a second OpenAlex lookup — the search already
    filtered on ``open_access.is_oa``, so ``is_oa=False`` here means "skip".
    """
    ident = ev.identifier or ""
    m = _ARXIV_RE.search(ident)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    if ev.oa_pdf_url:
        return ev.oa_pdf_url
    if ev.is_oa is False:
        return None  # search tier already confirmed non-OA
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


def _arxiv_id(ev: Evidence) -> str | None:
    m = _ARXIV_RE.search(ev.identifier or "")
    return m.group(1) if m else None


def _fetch_literature_text(ev: Evidence, timeout: float) -> str | None:
    """OA full text: arXiv LaTeX source when available, else the PDF.

    Source is tried first because the PDF path is where the time goes — a
    100-page arXiv paper measured ~53 s, of which ~50 s was RapidOCR firing on
    figure-heavy pages that look like scans to the triage heuristic, against
    ~1.2 s for the source. It also keeps equations as LaTeX and section
    structure as headings. PDF-only submissions fall through unchanged.
    """
    from .pdf_downloader import _extract_text, fetch_pdf

    arxiv_id = _arxiv_id(ev)
    if arxiv_id and getattr(get_settings(), "arxiv_prefer_source", True):
        from .arxiv_source import fetch_arxiv_markdown

        try:
            md = fetch_arxiv_markdown(arxiv_id, timeout)
        except Exception as exc:
            md = degrade_return(logger, exc, f"arxiv source fetch failed: {arxiv_id}", None)
        if md and len(md.strip()) > 200:
            return md

    pdf_url = _resolve_oa_pdf_url(ev, timeout)
    if not pdf_url:
        raise FetchError("无 OA 版本")
    pdf = fetch_pdf(pdf_url, timeout=timeout)
    if not pdf:
        raise FetchError("下载超时")
    text = _extract_text(pdf)
    if not text or len(text.strip()) <= 200:
        raise FetchError("解析为空")
    return text


def _extract_web_text(html: str) -> str:
    """Web body → Markdown via the unified parsing layer (trafilatura first)."""
    from .parsing import html_to_markdown

    return html_to_markdown(html)


def _fetch_web_text(ev: Evidence, timeout: float) -> str | None:
    from .ingestion import _is_safe_url

    url = (ev.identifier or "").strip()
    if not _is_safe_url(url):
        return None
    # follow_redirects=False + manual loop: re-check _is_safe_url on every
    # redirect target so an SSRF cannot pivot to an internal host via a 3xx.
    current_url = url
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=_HEADERS) as client:
            r = None
            for _hop in range(4):  # initial + up to 3 redirects
                r = client.get(current_url)
                # Treat any 3xx as a redirect (works with real httpx.Response
                # and simple fakes that only set status_code).
                if 300 <= int(getattr(r, "status_code", 0)) < 400:
                    location = r.headers.get("location")
                    if not location:
                        break
                    current_url = str(httpx.URL(current_url).join(location))
                    if not _is_safe_url(current_url):
                        logger.warning("web fulltext redirect blocked (SSRF guard): %s", current_url)
                        return None
                    continue
                break
            status = int(getattr(r, "status_code", 0)) if r is not None else 0
            # Recorded so the batch summary can tell "the server refused us"
            # apart from "the page came back nearly empty". Collapsing them
            # would let a wall of 403s argue for a JavaScript-rendering tier
            # that cannot fix a 403.
            _timing.note(http=status)
            if r is None or status != 200:
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
    """Split fetched full text into chunk Evidence rows preserving provenance.

    Structure-aware: fetched text is Markdown (trafilatura / PDF parsers), so
    heading paths survive into chunk titles and tables stay atomic — the same
    chunker every other ingest path uses.
    """
    from .chunking import chunk_markdown
    from .kb_index import chunk_snippet

    settings = get_settings()
    chunks = chunk_markdown(
        text,
        max_chars=settings.ingest_chunk_max_chars,
        overlap=settings.ingest_chunk_overlap,
    )
    chunks = [c for c in chunks if len(c.text.strip()) > 30][: settings.ingest_max_chunks]
    out: list[Evidence] = []
    for i, chunk in enumerate(chunks):
        title = ev.title if i == 0 else f"{ev.title} (p.{i + 1})"
        if chunk.heading_path:
            title = f"{title} · {chunk.heading_path}"
        out.append(
            Evidence(
                source=ev.source,
                identifier=f"{ev.identifier}#p{i}",
                title=title,
                snippet=chunk_snippet(chunk.text),
                relevance=max(0.2, round(ev.relevance - i * 0.01, 3)),
            )
        )
    return out


def _is_db_locked(exc: Exception) -> bool:
    """True when the exception chain carries a SQLite "database is locked".

    SQLAlchemy wraps the original OperationalError in "This Session's
    transaction has been rolled back due to a previous exception during flush",
    so a naive str() check on the top-level exception misses the lock. Walk the
    __cause__/__context__ chain to find it.
    """
    cur: BaseException | None = exc
    while cur is not None:
        if "database is locked" in str(cur).lower():
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _persist_fulltext(text: str, ev: Evidence, kind: str, *, project_id: str | None = None) -> str | None:
    """Store the raw full text as a SourceDocument (dedup by content hash).

    Retries the whole persist on SQLite "database is locked": kb_ingest writes
    into source_documents while uvicorn autosaves the project, and the two
    processes can exhaust the 60s busy_timeout. A fresh session + content-hash
    dedup make the retry idempotent — a create that already landed is found by
    find_by_hash on the next attempt instead of being duplicated.
    """
    import time

    from ..db.source_store import get_source_store

    content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    delay = 0.5
    for attempt in range(6):
        try:
            store = get_source_store()
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
                origin_url=(ev.identifier or "").strip()[:1024] or None,
                project_id=(project_id or None),
            )
            from .kb_index import index_source

            index_source(source_id, text)
            return source_id
        except Exception as exc:
            if _is_db_locked(exc) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
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
            # 下载失败（无 OA / 403 / 超时 / 解析为空）→ 移除，不进结果/左栏。
            continue
        out.append(ev)

    logger.info(
        "fulltext_fetcher: %d/%d succeeded %s",
        report.succeeded,
        report.attempted,
        report.by_kind,
    )
    return out, report
