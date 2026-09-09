"""Patent publication-number normalization (shared by ingest / KG / UI).

Accepts SCPN hyphenated ids (``CN-104789083-B``), compact pubs
(``CN104789083B``), and Google Patents URLs; returns a stable compact form
used as ``origin_url`` for KB dedup and fulltext fetch.
"""
from __future__ import annotations

import re

_OFFICES = ("CN", "US", "EP", "WO", "JP", "KR", "DE", "GB", "FR")
_COMPACT_RE = re.compile(
    r"^(CN|US|EP|WO|JP|KR|DE|GB|FR)([A-Z]?)(\d{4,}[A-Z0-9]*)$",
    re.I,
)


def normalize_patent_pub(raw: str | None) -> tuple[str | None, str | None]:
    """Return ``(office, compact_pub)`` e.g. ``('CN', 'CN104789083B')``."""
    if not raw:
        return None, None
    s = (raw or "").strip()
    s = s.split("?")[0].split("#")[0]
    upper = s.upper()
    if "PATENT/" in upper:
        s = s.rsplit("/", 1)[-1] if "/" in s else s
        # keep original casing for strip then upper
        upper = s.upper()
    # Strip separators for matching
    compact_try = re.sub(r"[\s\-_/]", "", upper)
    m = _COMPACT_RE.match(compact_try)
    if m:
        office = m.group(1).upper()
        return office, f"{office}{m.group(2).upper()}{m.group(3).upper()}"
    m2 = re.search(
        r"(CN|US|EP|WO|JP|KR|DE|GB|FR)[A-Z]?\d{4,}[A-Z0-9]*",
        compact_try,
        re.I,
    )
    if not m2:
        return None, None
    token = m2.group(0).upper()
    office = token[:2]
    return office, token


def hyphenated_scpn(compact: str | None) -> str | None:
    """Best-effort SCPN style ``CN-104789083-B`` from compact ``CN104789083B``."""
    if not compact:
        return None
    m = re.match(r"^([A-Z]{2})(\d+)([A-Z]\d*)?$", compact.upper())
    if not m:
        return None
    out = f"{m.group(1)}-{m.group(2)}"
    if m.group(3):
        out = f"{out}-{m.group(3)}"
    return out


def google_patents_url(compact_or_raw: str | None) -> str | None:
    office, compact = normalize_patent_pub(compact_or_raw)
    if not compact:
        return None
    return f"https://patents.google.com/patent/{compact}"


def patent_id_aliases(raw: str | None) -> list[str]:
    """All origin_url keys that should dedup to the same SourceDocument."""
    aliases: list[str] = []
    raw_s = (raw or "").strip()
    if raw_s:
        aliases.append(raw_s)
    office, compact = normalize_patent_pub(raw_s)
    if compact:
        aliases.append(compact)
        hyp = hyphenated_scpn(compact)
        if hyp:
            aliases.append(hyp)
        url = google_patents_url(compact)
        if url:
            aliases.append(url)
            # common trailing kind-less form already in compact
    # preserve order, unique
    seen: set[str] = set()
    out: list[str] = []
    for a in aliases:
        key = a.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key[:1024])
    return out


def canonical_origin_url(ev_identifier: str | None, *, url: str | None = None) -> str | None:
    """Preferred ``origin_url`` to persist: compact patent id, else DOI/url/ident."""
    for candidate in (ev_identifier, url):
        _office, compact = normalize_patent_pub(candidate)
        if compact:
            return compact
    for candidate in (ev_identifier, url):
        s = (candidate or "").strip()
        if s:
            return s[:1024]
    return None
