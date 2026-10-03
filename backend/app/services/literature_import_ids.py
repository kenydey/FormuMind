"""Wave E-Lit — paste DOI / ChemRxiv identifiers into literature library."""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any
from urllib.parse import quote

from . import literature_manifest as lm
from .scholar_helpers import extract_dois
from .http_safe import make_client

logger = logging.getLogger(__name__)

_ARXIV_RE = re.compile(
    r"(?i)\b(?:arxiv[:\s/]*|(?:https?://)?arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?\b"
)
_PMID_RE = re.compile(r"(?i)\bpmid[:\s]*(\d+)\b")
_CHEMRXIV_URL_RE = re.compile(
    r"(?i)chemrxiv\.org[^/\s]*/(?:engage/chemrxiv/(?:article-details|public-api/v1/items)/)?"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
_CHEMRXIV_PREFIX_RE = re.compile(
    r"(?i)\bchemrxiv:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
_UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)


def _openalex_work_by_doi(doi: str) -> dict[str, Any] | None:
    from .scholar_helpers import _openalex_get

    enc = quote(doi, safe="")
    return _openalex_get(
        f"https://api.openalex.org/works/doi:{enc}"
        f"?select=id,doi,title,publication_year,authorships,primary_location,ids"
    )


def _chemrxiv_public_item(item_id: str) -> dict[str, Any] | None:
    try:
        url = (
            "https://chemrxiv.org/engage/chemrxiv/public-api/v1/items/"
            + quote(item_id, safe="")
        )
        with make_client(timeout=8.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("chemrxiv public api failed: %s", exc)
        return None


def _authors_from_openalex(work: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in work.get("authorships") or []:
        if not isinstance(row, dict):
            continue
        author = row.get("author") or {}
        name = str(author.get("display_name") or "").strip()
        if name:
            out.append(name[:120])
    return out[:40]


def _item_from_openalex(work: dict[str, Any], *, fallback_doi: str | None = None) -> dict[str, Any]:
    doi = str(work.get("doi") or fallback_doi or "").replace("https://doi.org/", "").strip()
    doi = doi.lower() or None
    title = str(work.get("title") or doi or "untitled")[:240]
    year = work.get("publication_year")
    try:
        year_i = int(year) if year is not None else None
    except (TypeError, ValueError):
        year_i = None
    oaid = str(work.get("id") or "").rstrip("/").split("/")[-1] or None
    loc = work.get("primary_location") or {}
    url = None
    if isinstance(loc, dict):
        url = loc.get("landing_page_url") or (loc.get("source") or {}).get("homepage_url")
    chemrxiv_id = None
    ids = work.get("ids") or {}
    if isinstance(ids, dict):
        # ChemRxiv rarely in ids; infer from DOI prefix / URL
        pass
    if doi and "chemrxiv" in doi:
        # DOI like 10.26434/chemrxiv-2024-xxxx — keep chemrxiv scheme via DOI only
        pass
    if url and "chemrxiv.org" in str(url).lower():
        m = _CHEMRXIV_URL_RE.search(str(url))
        if m:
            chemrxiv_id = m.group(1).lower()
    item_id = f"doi:{doi}" if doi else f"openalex:{oaid or uuid.uuid4().hex[:10]}"
    return lm.ensure_item_library_fields(
        {
            "id": item_id,
            "title": title,
            "doi": doi,
            "authors": _authors_from_openalex(work),
            "year": year_i,
            "url": str(url)[:500] if url else None,
            "openalex_id": oaid,
            "chemrxiv_id": chemrxiv_id,
            "source": "ChemRxiv" if (doi and "chemrxiv" in doi) or chemrxiv_id else "import_ids",
            "snippet": "",
            "evidence_class": "import_ids",
            "screening": "unset",
        }
    )


def _item_from_chemrxiv_api(raw: dict[str, Any], *, item_id: str) -> dict[str, Any]:
    # public-api shape varies; tolerate common fields
    item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
    title = str(item.get("title") or "ChemRxiv preprint")[:240]
    doi = item.get("doi") or item.get("DOI")
    if isinstance(doi, str):
        doi = doi.replace("https://doi.org/", "").strip().lower() or None
    else:
        doi = None
    authors: list[str] = []
    for a in item.get("authors") or item.get("author") or []:
        if isinstance(a, dict):
            name = (
                a.get("name")
                or " ".join(
                    str(x)
                    for x in (a.get("firstName"), a.get("lastName"))
                    if x
                ).strip()
            )
            if name:
                authors.append(str(name)[:120])
        elif isinstance(a, str) and a.strip():
            authors.append(a.strip()[:120])
    year = None
    for key in ("publishedDate", "postedDate", "date", "year"):
        val = item.get(key)
        if not val:
            continue
        m = re.search(r"(20\d{2}|19\d{2})", str(val))
        if m:
            year = int(m.group(1))
            break
    url = (
        item.get("assetUrl")
        or item.get("url")
        or f"https://chemrxiv.org/engage/chemrxiv/article-details/{item_id}"
    )
    iid = f"doi:{doi}" if doi else f"chemrxiv:{item_id.lower()}"
    return lm.ensure_item_library_fields(
        {
            "id": iid,
            "title": title,
            "doi": doi,
            "authors": authors[:40],
            "year": year,
            "url": str(url)[:500] if url else None,
            "chemrxiv_id": item_id.lower(),
            "source": "ChemRxiv",
            "snippet": str(item.get("abstract") or "")[:400],
            "evidence_class": "import_ids",
            "screening": "unset",
        }
    )


def _item_bare(*, doi: str | None = None, chemrxiv_id: str | None = None) -> dict[str, Any]:
    doi_n = doi.lower().strip() if doi else None
    crx = chemrxiv_id.lower().strip() if chemrxiv_id else None
    title = doi_n or crx or "untitled"
    iid = f"doi:{doi_n}" if doi_n else f"chemrxiv:{crx}"
    return lm.ensure_item_library_fields(
        {
            "id": iid,
            "title": title[:240],
            "doi": doi_n,
            "chemrxiv_id": crx,
            "source": "ChemRxiv" if (doi_n and "chemrxiv" in doi_n) or crx else "import_ids",
            "snippet": "",
            "evidence_class": "import_ids",
            "screening": "unset",
            "authors": [],
            "year": None,
        }
    )


def parse_import_tokens(text: str) -> dict[str, Any]:
    """Split paste text into doi / chemrxiv tokens and unsupported failures."""
    text = text or ""
    failures: list[dict[str, str]] = []
    for m in _ARXIV_RE.finditer(text):
        failures.append(
            {"token": m.group(0), "reason": "unsupported_scheme", "detail": "arxiv"}
        )
    for m in _PMID_RE.finditer(text):
        failures.append(
            {"token": m.group(0), "reason": "unsupported_scheme", "detail": "pmid"}
        )
    dois = extract_dois(text)
    chemrxiv_ids: list[str] = []
    for m in _CHEMRXIV_PREFIX_RE.finditer(text):
        cid = m.group(1).lower()
        if cid not in chemrxiv_ids:
            chemrxiv_ids.append(cid)
    for m in _CHEMRXIV_URL_RE.finditer(text):
        cid = m.group(1).lower()
        if cid not in chemrxiv_ids:
            chemrxiv_ids.append(cid)
    # Bare UUIDs only if chemrxiv mentioned in text (avoid eating random UUIDs)
    if "chemrxiv" in text.lower():
        for m in _UUID_RE.finditer(text):
            cid = m.group(1).lower()
            if cid not in chemrxiv_ids:
                chemrxiv_ids.append(cid)
    # Drop chemrxiv ids that are already covered solely as DOI tokens — keep both
    return {"dois": dois, "chemrxiv_ids": chemrxiv_ids[:40], "failures": failures}


def resolve_doi_item(doi: str) -> dict[str, Any]:
    work = _openalex_work_by_doi(doi)
    if work:
        return _item_from_openalex(work, fallback_doi=doi)
    return _item_bare(doi=doi)


def resolve_chemrxiv_item(chemrxiv_id: str) -> dict[str, Any]:
    raw = _chemrxiv_public_item(chemrxiv_id)
    if raw:
        item = _item_from_chemrxiv_api(raw, item_id=chemrxiv_id)
        # Try enrich via DOI if present
        if item.get("doi"):
            work = _openalex_work_by_doi(str(item["doi"]))
            if work:
                enriched = _item_from_openalex(work, fallback_doi=str(item["doi"]))
                enriched["chemrxiv_id"] = chemrxiv_id.lower()
                return lm.ensure_item_library_fields(enriched)
        return item
    return _item_bare(chemrxiv_id=chemrxiv_id)


def import_ids(
    project_id: str,
    text: str,
    *,
    actor: str = "user",
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not lm.library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")

    parsed = parse_import_tokens(text)
    added: list[str] = []
    skipped: list[str] = []
    failures: list[dict[str, str]] = list(parsed["failures"])
    man = lm.load_manifest(project_id)

    for doi in parsed["dois"]:
        try:
            item = resolve_doi_item(doi)
            man, status = lm.upsert_library_item(
                project_id, item, settings=settings, clear_freeze_on_add=True
            )
            if status == "added":
                added.append(str(item["id"]))
            else:
                skipped.append(doi)
        except Exception as exc:  # noqa: BLE001
            failures.append({"token": doi, "reason": "resolve_failed", "detail": str(exc)[:200]})

    for cid in parsed["chemrxiv_ids"]:
        # Skip if DOI import already covered this ChemRxiv id
        if lm.find_item_by_identifier(man, scheme="chemrxiv", value=cid):
            skipped.append(f"chemrxiv:{cid}")
            continue
        try:
            item = resolve_chemrxiv_item(cid)
            man, status = lm.upsert_library_item(
                project_id, item, settings=settings, clear_freeze_on_add=True
            )
            if status == "added":
                added.append(str(item["id"]))
            else:
                skipped.append(f"chemrxiv:{cid}")
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {"token": f"chemrxiv:{cid}", "reason": "resolve_failed", "detail": str(exc)[:200]}
            )

    man = lm.load_manifest(project_id)
    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "import_ids",
            "at": time.time(),
            "actor": actor or "user",
            "added": len(added),
            "skipped": len(skipped),
            "failed": len(failures),
        }
    ]
    man = lm.save_manifest(man)
    return {
        "project_id": project_id,
        "added": added,
        "skipped": skipped,
        "failures": failures,
        "manifest": man,
    }
