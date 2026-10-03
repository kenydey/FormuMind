"""Project-scoped literature manifest + frozen corpus (Wave B / AIPOCH subset)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
SCHEMA_VERSION = 2

LIBRARY_PATCH_FIELDS = frozenset(
    {
        "title",
        "doi",
        "authors",
        "year",
        "url",
        "oa_pdf_url",
        "tags",
        "notes",
        "screening",
        "collection_ids",
        "chemrxiv_id",
        "openalex_id",
        "snippet",
    }
)
IDENTITY_FIELDS = frozenset({"doi", "title", "chemrxiv_id"})
SCREENING_VALUES = frozenset({"match", "no_match", "uncertain", "unset"})


def _data_root() -> Path:
    return Path("./data").resolve()


def _safe_project(project_id: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", (project_id or "").strip())[:120] or "unknown"


def manifest_path(project_id: str) -> Path:
    return _data_root() / "literature" / _safe_project(project_id) / "manifest.json"


def manifest_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "literature_manifest_enabled", True))


def library_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "literature_library_enabled", False))


def empty_manifest(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "schema_version": SCHEMA_VERSION,
        "captured_at": None,
        "content_hash": "",
        "retrievals": [],
        "items": [],
        "collections": [],
        "frozen": None,
        "coverage": {"candidate_count": 0, "frozen_count": 0},
        "events": [],
    }


def _now() -> float:
    return time.time()


def normalize_tag(tag: str) -> str:
    return re.sub(r"\s+", " ", (tag or "").strip().lower())[:64]


def ensure_item_library_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Fill schema-v2 library defaults in-place; return the item."""
    now = _now()
    item.setdefault("authors", [])
    if not isinstance(item.get("authors"), list):
        item["authors"] = []
    item.setdefault("year", None)
    item.setdefault("chemrxiv_id", None)
    item.setdefault("openalex_id", None)
    item.setdefault("tags", [])
    if not isinstance(item.get("tags"), list):
        item["tags"] = []
    item["tags"] = [normalize_tag(t) for t in item["tags"] if normalize_tag(t)][:32]
    item.setdefault("notes", "")
    if not isinstance(item.get("notes"), str):
        item["notes"] = str(item.get("notes") or "")[:4000]
    item.setdefault("collection_ids", [])
    if not isinstance(item.get("collection_ids"), list):
        item["collection_ids"] = []
    item.setdefault("identifiers", [])
    if not isinstance(item.get("identifiers"), list):
        item["identifiers"] = []
    item.setdefault("created_at", now)
    item.setdefault("updated_at", item.get("created_at") or now)
    # Keep identifiers in sync with top-level doi / chemrxiv / openalex when present.
    _sync_identifiers_from_fields(item)
    return item


def _sync_identifiers_from_fields(item: dict[str, Any]) -> None:
    ids = list(item.get("identifiers") or [])
    by_scheme: dict[str, str] = {}
    for row in ids:
        if not isinstance(row, dict):
            continue
        scheme = str(row.get("scheme") or "").strip().lower()
        value = str(row.get("value") or "").strip()
        if scheme and value:
            by_scheme[scheme] = value
    doi = str(item.get("doi") or "").strip()
    if doi:
        by_scheme["doi"] = doi.lower()
        item["doi"] = by_scheme["doi"]
    crx = str(item.get("chemrxiv_id") or "").strip()
    if crx:
        by_scheme["chemrxiv"] = crx.lower()
        item["chemrxiv_id"] = by_scheme["chemrxiv"]
    oaid = str(item.get("openalex_id") or "").strip()
    if oaid:
        by_scheme["openalex"] = oaid
        item["openalex_id"] = oaid
    item["identifiers"] = [
        {"scheme": k, "value": v}
        for k, v in sorted(by_scheme.items())
        if k in {"doi", "chemrxiv", "openalex"} and v
    ]


def migrate_manifest(raw: dict[str, Any]) -> dict[str, Any]:
    raw.setdefault("collections", [])
    if not isinstance(raw.get("collections"), list):
        raw["collections"] = []
    items = []
    for it in raw.get("items") or []:
        if isinstance(it, dict):
            items.append(ensure_item_library_fields(dict(it)))
    raw["items"] = items
    raw["schema_version"] = SCHEMA_VERSION
    return raw


def compute_digest(item_ids: list[str], items: list[dict[str, Any]]) -> str:
    by_id = {str(i.get("id")): i for i in items}
    parts: list[str] = []
    for iid in sorted(set(item_ids)):
        row = by_id.get(iid) or {}
        parts.append(
            f"{iid}|{row.get('doi') or ''}|{row.get('title') or ''}"
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def content_hash_manifest(manifest: dict[str, Any]) -> str:
    payload = {
        "items": manifest.get("items") or [],
        "frozen": manifest.get("frozen"),
        "retrievals": manifest.get("retrievals") or [],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load_manifest(project_id: str) -> dict[str, Any]:
    path = manifest_path(project_id)
    if not path.is_file():
        return empty_manifest(project_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return empty_manifest(project_id)
        raw.setdefault("project_id", project_id)
        raw.setdefault("items", [])
        raw.setdefault("retrievals", [])
        raw.setdefault("events", [])
        raw.setdefault("coverage", {"candidate_count": 0, "frozen_count": 0})
        return migrate_manifest(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("literature manifest load failed: %s", exc)
        # Keep the unreadable file: the next save_manifest would otherwise
        # overwrite whatever (possibly recoverable) corpus it held.
        try:
            os.replace(path, path.with_name(path.name + ".corrupt"))
        except OSError:
            pass
        return empty_manifest(project_id)


def save_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    pid = str(manifest.get("project_id") or "")
    path = manifest_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    items = list(manifest.get("items") or [])
    frozen = manifest.get("frozen")
    frozen_count = len((frozen or {}).get("item_ids") or []) if frozen else 0
    manifest["coverage"] = {
        "candidate_count": len(items),
        "frozen_count": frozen_count,
    }
    manifest["content_hash"] = content_hash_manifest(manifest)
    manifest["schema_version"] = SCHEMA_VERSION
    with _LOCK:
        # Atomic replace: a plain write_text truncates first, so a concurrent
        # reader could see half a file, fail to parse it, fall back to an empty
        # manifest and then save that over the frozen corpus.
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
    return manifest


def get_frozen(project_id: str) -> dict[str, Any] | None:
    man = load_manifest(project_id)
    frozen = man.get("frozen")
    if not frozen or not (frozen.get("item_ids") or []):
        return None
    return man


def frozen_items(project_id: str) -> list[dict[str, Any]]:
    man = get_frozen(project_id)
    if not man:
        return []
    ids = set(man["frozen"]["item_ids"])
    return [i for i in (man.get("items") or []) if i.get("id") in ids]


def _merge_capture_preserve(prev: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Keep library/screening fields when Capture refreshes a row by id."""
    if prev.get("screening") not in (None, "unset"):
        item["screening"] = prev["screening"]
        # P0-8: carry who/what made the call, or a human decision loses its
        # "human" marker on re-capture and auto-screening may overwrite it.
        for key in ("screening_source", "screening_by", "screening_at"):
            if key in prev:
                item[key] = prev[key]
    for key in (
        # W4-6 citation locator (annotation metadata) survives a re-capture too.
        "locator",
        "locator_by",
        "locator_at",
        "tags",
        "notes",
        "authors",
        "year",
        "chemrxiv_id",
        "openalex_id",
        "collection_ids",
        "identifiers",
        "created_at",
        "has_fulltext",
        "oa_pdf_url",
        "url",
        "enrich_status",
    ):
        if prev.get(key) not in (None, "", []) and item.get(key) in (None, "", []):
            item[key] = prev[key]
    return ensure_item_library_fields(item)


def _item_from_source_doc(doc: Any) -> dict[str, Any]:
    sid = str(getattr(doc, "id", "") or "")
    title = (getattr(doc, "title", None) or getattr(doc, "origin_url", None) or sid)[:240]
    guide = getattr(doc, "source_guide", None) or {}
    snippet = ""
    doi = None
    if isinstance(guide, dict):
        snippet = str(guide.get("summary") or "")[:400]
        doi = guide.get("doi")
    return ensure_item_library_fields(
        {
            "id": sid or title,
            "title": title,
            "doi": doi,
            "source": "project_source",
            "snippet": snippet,
            "evidence_class": "project_source",
            "screening": "unset",
        }
    )


def _item_from_evidence(ev: Any) -> dict[str, Any]:
    ident = str(getattr(ev, "identifier", "") or getattr(ev, "title", "") or "")
    title = str(getattr(ev, "title", None) or ident)[:240]
    snippet = str(getattr(ev, "snippet", None) or "")[:400]
    # Evidence has no `doi` field — the DOI (when there is one) is the
    # identifier. Without it, OA enrichment (needs doi/oa_pdf_url), the DOI
    # requirement and the year filters never saw a single search hit.
    doi = getattr(ev, "doi", None)
    if not doi and _DOI_RE.search(ident):
        doi = _DOI_RE.search(ident).group(0)
    year = getattr(ev, "pub_year", None)
    if year is None:
        pub_date = str(getattr(ev, "pub_date", "") or "")
        if pub_date[:4].isdigit():
            year = int(pub_date[:4])
    item = ensure_item_library_fields(
        {
            "id": ident or title,
            "title": title,
            "doi": doi,
            "year": year,
            "source": str(getattr(ev, "source", "") or "search_hit"),
            "snippet": snippet,
            "evidence_class": "search_hit",
            "screening": "unset",
        }
    )
    oa_pdf_url = getattr(ev, "oa_pdf_url", None)
    if oa_pdf_url:
        item["oa_pdf_url"] = oa_pdf_url
    if getattr(ev, "has_fulltext", None):
        item["has_fulltext"] = True
    return item


def _clear_freeze(man: dict[str, Any], *, reason: str) -> None:
    if not man.get("frozen"):
        return
    man["frozen"] = None
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "unfrozen", "at": _now(), "reason": reason}
    ]


def _item_matches_query(item: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    blob = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("doi") or ""),
            str(item.get("chemrxiv_id") or ""),
            str(item.get("notes") or ""),
            str(item.get("snippet") or ""),
            " ".join(str(a) for a in (item.get("authors") or [])),
            " ".join(str(t) for t in (item.get("tags") or [])),
        ]
    ).lower()
    return q in blob


def list_library(
    project_id: str,
    *,
    q: str = "",
    tag: str = "",
    collection_id: str = "",
    screening: str = "",
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = load_manifest(project_id)
    q_norm = (q or "").strip().lower()
    tag_norm = normalize_tag(tag) if tag else ""
    coll = (collection_id or "").strip()
    scr = (screening or "").strip()
    items_out: list[dict[str, Any]] = []
    for item in man.get("items") or []:
        if scr and (item.get("screening") or "unset") != scr:
            continue
        if tag_norm and tag_norm not in (item.get("tags") or []):
            continue
        if coll and coll not in (item.get("collection_ids") or []):
            continue
        if not _item_matches_query(item, q_norm):
            continue
        items_out.append(item)
    return {
        "project_id": project_id,
        "items": items_out,
        "collections": list(man.get("collections") or []),
        "coverage": man.get("coverage") or {},
        "frozen": man.get("frozen"),
        "schema_version": man.get("schema_version"),
        "content_hash": man.get("content_hash"),
    }


def patch_library_item(
    project_id: str,
    item_id: str,
    patch: dict[str, Any],
    *,
    settings: Any = None,
    require_library: bool = True,
    actor: str = "user",
) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if require_library and settings is not None and not library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    patch = {k: v for k, v in (patch or {}).items() if k in LIBRARY_PATCH_FIELDS}
    if not patch:
        raise ValueError("empty patch")
    man = load_manifest(project_id)
    target: dict[str, Any] | None = None
    for item in man.get("items") or []:
        if str(item.get("id")) == item_id:
            target = item
            break
    if target is None:
        raise LookupError("item not found")
    ensure_item_library_fields(target)
    before_identity = {
        k: str(target.get(k) or "").strip().lower() for k in IDENTITY_FIELDS
    }
    if "screening" in patch:
        scr = str(patch["screening"] or "").strip()
        if scr not in SCREENING_VALUES:
            raise ValueError("invalid screening disposition")
        target["screening"] = scr
        # P0-8: a manual disposition must not be overwritten by auto-screening.
        target["screening_source"] = "human"
        target["screening_by"] = (actor or "user").strip() or "user"
        target["screening_at"] = time.time()
    if "title" in patch and patch["title"] is not None:
        target["title"] = str(patch["title"])[:240]
    if "doi" in patch:
        doi = patch["doi"]
        target["doi"] = str(doi).strip().lower() if doi else None
    if "chemrxiv_id" in patch:
        crx = patch["chemrxiv_id"]
        target["chemrxiv_id"] = str(crx).strip().lower() if crx else None
    if "openalex_id" in patch:
        oaid = patch["openalex_id"]
        target["openalex_id"] = str(oaid).strip() if oaid else None
    if "authors" in patch:
        authors = patch["authors"]
        if authors is None:
            target["authors"] = []
        elif not isinstance(authors, list):
            raise ValueError("authors must be a list")
        else:
            target["authors"] = [str(a).strip()[:120] for a in authors if str(a).strip()][
                :40
            ]
    if "year" in patch:
        year = patch["year"]
        if year is None or year == "":
            target["year"] = None
        else:
            try:
                yi = int(year)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid year") from exc
            if yi < 1000 or yi > 3000:
                raise ValueError("invalid year")
            target["year"] = yi
    if "url" in patch:
        target["url"] = str(patch["url"] or "").strip()[:500] or None
    if "oa_pdf_url" in patch:
        target["oa_pdf_url"] = str(patch["oa_pdf_url"] or "").strip()[:500] or None
    if "snippet" in patch and patch["snippet"] is not None:
        target["snippet"] = str(patch["snippet"])[:800]
    if "tags" in patch:
        tags = patch["tags"]
        if tags is None:
            target["tags"] = []
        elif not isinstance(tags, list):
            raise ValueError("tags must be a list")
        else:
            seen: list[str] = []
            for t in tags:
                nt = normalize_tag(str(t))
                if nt and nt not in seen:
                    seen.append(nt)
            target["tags"] = seen[:32]
    if "notes" in patch and patch["notes"] is not None:
        target["notes"] = str(patch["notes"])[:4000]
    if "collection_ids" in patch:
        cids = patch["collection_ids"]
        if cids is None:
            target["collection_ids"] = []
        elif not isinstance(cids, list):
            raise ValueError("collection_ids must be a list")
        else:
            known = {str(c.get("id")) for c in (man.get("collections") or [])}
            target["collection_ids"] = [
                str(c) for c in cids if str(c) in known
            ]
            # Keep collection.item_ids in sync
            iid = str(target.get("id"))
            for coll in man.get("collections") or []:
                members = [str(x) for x in (coll.get("item_ids") or [])]
                if str(coll.get("id")) in target["collection_ids"]:
                    if iid not in members:
                        members.append(iid)
                else:
                    members = [x for x in members if x != iid]
                coll["item_ids"] = members
                coll["updated_at"] = _now()
    _sync_identifiers_from_fields(target)
    target["updated_at"] = _now()
    after_identity = {
        k: str(target.get(k) or "").strip().lower() for k in IDENTITY_FIELDS
    }
    identity_changed = before_identity != after_identity
    screening_changed = "screening" in patch
    if identity_changed or screening_changed:
        reason = "screening_change" if screening_changed else "identity_change"
        _clear_freeze(man, reason=reason)
    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "item_patched",
            "at": _now(),
            "item_id": item_id,
            "fields": sorted(patch.keys()),
        }
    ]
    return save_manifest(man)


def create_collection(
    project_id: str,
    name: str,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    name = re.sub(r"\s+", " ", (name or "").strip())[:120]
    if not name:
        raise ValueError("collection name required")
    man = load_manifest(project_id)
    import uuid

    cid = uuid.uuid4().hex[:12]
    now = _now()
    man.setdefault("collections", []).append(
        {
            "id": cid,
            "name": name,
            "item_ids": [],
            "created_at": now,
            "updated_at": now,
        }
    )
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "collection_created", "at": now, "collection_id": cid, "name": name}
    ]
    return save_manifest(man)


def patch_collection(
    project_id: str,
    collection_id: str,
    *,
    name: str | None = None,
    item_ids: list[str] | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = load_manifest(project_id)
    coll = None
    for c in man.get("collections") or []:
        if str(c.get("id")) == collection_id:
            coll = c
            break
    if coll is None:
        raise LookupError("collection not found")
    if name is not None:
        nm = re.sub(r"\s+", " ", name.strip())[:120]
        if not nm:
            raise ValueError("collection name required")
        coll["name"] = nm
    if item_ids is not None:
        known_items = {str(i.get("id")) for i in (man.get("items") or [])}
        members = [str(x) for x in item_ids if str(x) in known_items]
        coll["item_ids"] = members
        # Sync item.collection_ids
        for item in man.get("items") or []:
            ensure_item_library_fields(item)
            cids = [str(x) for x in (item.get("collection_ids") or [])]
            iid = str(item.get("id"))
            if iid in members:
                if collection_id not in cids:
                    cids.append(collection_id)
            else:
                cids = [x for x in cids if x != collection_id]
            item["collection_ids"] = cids
    coll["updated_at"] = _now()
    # collection rename / membership does not clear freeze
    return save_manifest(man)


def delete_collection(
    project_id: str,
    collection_id: str,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = load_manifest(project_id)
    before = list(man.get("collections") or [])
    after = [c for c in before if str(c.get("id")) != collection_id]
    if len(after) == len(before):
        raise LookupError("collection not found")
    man["collections"] = after
    for item in man.get("items") or []:
        cids = [str(x) for x in (item.get("collection_ids") or [])]
        item["collection_ids"] = [x for x in cids if x != collection_id]
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "collection_deleted", "at": _now(), "collection_id": collection_id}
    ]
    return save_manifest(man)


def find_item_by_identifier(
    man: dict[str, Any], *, scheme: str, value: str
) -> dict[str, Any] | None:
    scheme = (scheme or "").strip().lower()
    value = (value or "").strip().lower()
    if not scheme or not value:
        return None
    for item in man.get("items") or []:
        ensure_item_library_fields(item)
        for row in item.get("identifiers") or []:
            if (
                str(row.get("scheme") or "").lower() == scheme
                and str(row.get("value") or "").lower() == value
            ):
                return item
        if scheme == "doi" and str(item.get("doi") or "").lower() == value:
            return item
        if scheme == "chemrxiv" and str(item.get("chemrxiv_id") or "").lower() == value:
            return item
        if scheme == "openalex" and str(item.get("openalex_id") or "") == value:
            return item
    return None


def upsert_library_item(
    project_id: str,
    item: dict[str, Any],
    *,
    settings: Any = None,
    clear_freeze_on_add: bool = True,
) -> tuple[dict[str, Any], str]:
    """Insert or skip-duplicate by identifiers. Returns (manifest, status)."""
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = load_manifest(project_id)
    item = ensure_item_library_fields(dict(item))
    for row in item.get("identifiers") or []:
        scheme = str(row.get("scheme") or "")
        value = str(row.get("value") or "")
        existing = find_item_by_identifier(man, scheme=scheme, value=value)
        if existing is not None:
            return man, "skipped_duplicate"
    iid = str(item.get("id") or "").strip()
    if not iid:
        raise ValueError("item id required")
    if any(str(i.get("id")) == iid for i in (man.get("items") or [])):
        return man, "skipped_duplicate"
    man.setdefault("items", []).append(item)
    if clear_freeze_on_add:
        _clear_freeze(man, reason="library_import")
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "item_imported", "at": _now(), "item_id": iid}
    ]
    return save_manifest(man), "added"


def capture_from_project(
    project_id: str,
    *,
    query: str = "",
    settings: Any = None,
) -> dict[str, Any]:
    """Capture candidates from project sources + workspace search hits."""
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    man = load_manifest(project_id)
    by_id: dict[str, dict[str, Any]] = {str(i["id"]): i for i in (man.get("items") or [])}
    new_ids: list[str] = []

    try:
        from ..db.source_store import get_source_store

        for doc in get_source_store().list_for_project(project_id, limit=80):
            item = _item_from_source_doc(doc)
            if not item["id"]:
                continue
            prev = by_id.get(item["id"])
            if prev:
                item = _merge_capture_preserve(prev, item)
            by_id[item["id"]] = item
            new_ids.append(item["id"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("capture source_store skipped: %s", exc)

    try:
        from ..db.project_store import get_project_store

        detail = get_project_store().get(project_id)
        ws = getattr(detail, "workspace", None) if detail else None
        for ev in (getattr(ws, "sources", None) or [])[:40]:
            item = _item_from_evidence(ev)
            if not item["id"]:
                continue
            prev = by_id.get(item["id"])
            if prev:
                item = _merge_capture_preserve(prev, item)
            by_id[item["id"]] = item
            new_ids.append(item["id"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("capture workspace sources skipped: %s", exc)

    man["items"] = list(by_id.values())
    # W1-4 钩子：跨源候选 union-find 去重（literature_identity 未就绪时跳过，fail-open）
    try:
        from .literature_identity import dedupe_items as _dedupe_items
    except ImportError:
        _dedupe_items = None
    if _dedupe_items is not None:
        try:
            deduped, merged = _dedupe_items(man["items"])
            if merged:
                logger.info("capture dedupe merged %d items", len(merged))
                man["events"] = (man.get("events") or [])[-180:] + [
                    {"type": "deduped", "at": time.time(), "merged": len(merged)}
                ]
            man["items"] = deduped
        except Exception as exc:  # noqa: BLE001
            logger.debug("capture dedupe skipped: %s", exc)
    man["captured_at"] = time.time()
    man.setdefault("retrievals", []).append(
        {
            "query": query or "",
            "at": time.time(),
            "candidate_ids": sorted(set(new_ids)),
        }
    )
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "captured", "at": time.time(), "count": len(man["items"])}
    ]
    # Capture invalidates freeze (corpus may have changed)
    if man.get("frozen"):
        man["frozen"] = None
        man["events"].append({"type": "unfrozen", "at": time.time(), "reason": "recapture"})
    return save_manifest(man)


def freeze(
    project_id: str,
    *,
    item_ids: list[str] | None = None,
    actor: str = "user",
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    man = load_manifest(project_id)
    items = list(man.get("items") or [])
    if not items:
        raise ValueError("no candidates — capture first")
    by_id = {str(i["id"]): i for i in items}
    if item_ids:
        chosen = [i for i in item_ids if i in by_id]
    else:
        # Default: all match|unset and not no_match
        chosen = [
            str(i["id"])
            for i in items
            if (i.get("screening") or "unset") in {"match", "unset", "uncertain"}
            and (i.get("screening") or "") != "no_match"
        ]
        # Prefer match over unset if any match exists
        matches = [
            str(i["id"])
            for i in items
            if (i.get("screening") or "") == "match"
        ]
        if matches:
            chosen = matches
    if not chosen:
        raise ValueError("no freezeable item_ids")
    if len(chosen) > len(items):
        raise ValueError("frozen count exceeds candidates")
    digest = compute_digest(chosen, items)
    man["frozen"] = {
        "at": time.time(),
        "actor": (actor or "user").strip() or "user",
        "item_ids": sorted(set(chosen)),
        "digest": digest,
    }
    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "frozen",
            "at": time.time(),
            "actor": man["frozen"]["actor"],
            "count": len(chosen),
            "digest": digest[:16],
        }
    ]
    return save_manifest(man)


def unfreeze(project_id: str, *, actor: str = "user", settings: Any = None) -> dict[str, Any]:
    if settings is not None and not manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    man = load_manifest(project_id)
    man["frozen"] = None
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "unfrozen", "at": time.time(), "actor": actor or "user"}
    ]
    return save_manifest(man)


def update_item_screening(
    project_id: str,
    item_id: str,
    screening: str,
    *,
    actor: str = "user",
) -> dict[str, Any]:
    """人工改动单条筛选结论；P0-8：标记 screening_source="human" 以免被自动筛选覆盖。"""
    if screening not in {"match", "no_match", "uncertain", "unset"}:
        raise ValueError("invalid screening disposition")
    return patch_library_item(
        project_id,
        item_id,
        {"screening": screening},
        require_library=False,
        actor=actor,
    )


def set_item_locator(
    project_id: str,
    item_id: str,
    locator: dict[str, Any] | None,
    *,
    actor: str = "user",
) -> dict[str, Any]:
    """W4-6 · P0-15: 给 manifest item 写引用定位器 ``{"page","figure","table"}``。

    * 只保留非空字段；``locator`` 为空/全空 → 清除已有 locator。
    * locator 是**标注元数据**（非 corpus 成员变更）：不清除 freeze，
      freeze 摘要（``compute_digest`` 只覆盖 id|doi|title）不受影响；
      freeze 后仍可精化定位器，供 W4-2 ``evidence_json`` 读取。
    * 非法 page（如非数字字符串）→ ValueError。
    """
    man = load_manifest(project_id)
    norm = _normalize_locator(locator)  # raises ValueError on bad input
    found = False
    for item in man.get("items") or []:
        if str(item.get("id")) == str(item_id):
            if norm:
                item["locator"] = norm
            else:
                item.pop("locator", None)
            item["locator_by"] = (actor or "user").strip() or "user"
            item["locator_at"] = time.time()
            found = True
            break
    if not found:
        raise LookupError("item not found")
    man["events"] = (man.get("events") or [])[-180:] + [
        {"type": "locator_set", "at": time.time(), "item_id": str(item_id)}
    ]
    return save_manifest(man)


def get_item_locator(project_id: str, item_id: str) -> dict[str, Any] | None:
    """W4-6 · P0-15: 读 manifest item 的 locator（无 → None）。"""
    man = load_manifest(project_id)
    for item in man.get("items") or []:
        if str(item.get("id")) == str(item_id):
            loc = item.get("locator")
            return dict(loc) if isinstance(loc, dict) else None
    return None


def _normalize_locator(locator: dict[str, Any] | None) -> dict[str, Any]:
    """校验并紧凑化 locator dict；非法输入抛 ValueError。"""
    if not locator:
        return {}
    if not isinstance(locator, dict):
        raise ValueError("locator must be a dict")
    out: dict[str, Any] = {}
    page = locator.get("page")
    if page is not None:
        try:
            page_i = int(page)
        except (TypeError, ValueError):
            raise ValueError(f"invalid locator.page: {page!r}")
        if page_i < 1:
            raise ValueError(f"invalid locator.page: {page!r}")
        out["page"] = page_i
    for key in ("figure", "table"):
        val = locator.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()
    return out


def literature_slice_from_frozen(project_id: str) -> dict[str, Any] | None:
    """Return dossier-style literature slice if frozen; else None."""
    items = frozen_items(project_id)
    if not items:
        return None
    man = load_manifest(project_id)
    rows = []
    source_ids = []
    for it in items:
        sid = str(it.get("id") or "")
        if sid:
            source_ids.append(sid)
        rows.append(
            {
                "cluster": "frozen_corpus",
                "title": (it.get("title") or sid)[:120],
                "source_id": sid,
                "snippet": (it.get("snippet") or "")[:80],
                "l1": "",
                "doi": it.get("doi"),
            }
        )
    return {
        "rows": rows,
        "source_ids": source_ids,
        "frozen": True,
        "digest": (man.get("frozen") or {}).get("digest"),
        "count": len(rows),
    }


def preflight_corpus_findings(
    project_id: str,
    markdown: str,
    *,
    settings: Any,
    cite_source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Extra preflight findings for frozen corpus (dicts compatible with Finding fields)."""
    import uuid

    findings: list[dict[str, Any]] = []
    if not manifest_enabled(settings):
        return findings
    man = load_manifest(project_id)
    frozen = man.get("frozen")
    require = bool(getattr(settings, "frozen_corpus_required_for_export", False))
    if require and not frozen:
        findings.append(
            {
                "id": uuid.uuid4().hex[:12],
                "check": "corpus",
                "severity": "blocking",
                "status": "open",
                "title": "导出要求已冻结文献语料",
                "detail": "frozen_corpus_required_for_export=true 但尚无 freeze",
                "evidence": [],
                "location": {},
            }
        )
        return findings
    if not frozen:
        return findings

    frozen_ids = set(frozen.get("item_ids") or [])
    items = {str(i.get("id")): i for i in (man.get("items") or [])}
    dois = {
        str(i.get("doi")).lower()
        for i in items.values()
        if i.get("id") in frozen_ids and i.get("doi")
    }

    # corpus_cite: cited source ids not in frozen
    for sid in cite_source_ids or []:
        if sid and sid not in frozen_ids:
            findings.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "check": "corpus_cite",
                    "severity": "blocking",
                    "status": "open",
                    "title": f"引用源不在冻结语料: {sid[:80]}",
                    "detail": "source_id not in frozen.item_ids",
                    "evidence": [sid],
                    "location": {},
                }
            )

    # DOI mentions in footnotes vs frozen dois (best-effort)
    for m in re.finditer(r"10\.\d{4,9}/[^\s\]\)\"']+", markdown or "", re.I):
        doi = m.group(0).lower().rstrip(".,;")
        if dois and doi not in dois:
            findings.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "check": "corpus_cite",
                    "severity": "blocking",
                    "status": "open",
                    "title": f"DOI 不在冻结语料: {doi}",
                    "detail": doi,
                    "evidence": [doi],
                    "location": {},
                }
            )

    if bool(getattr(settings, "literature_screening_required_for_export", False)):
        unset = [
            i
            for i in (man.get("items") or [])
            if (i.get("screening") or "unset") == "unset"
            and str(i.get("id")) in frozen_ids
        ]
        if unset:
            findings.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "check": "unscreened_open",
                    "severity": "major",
                    "status": "open",
                    "title": f"{len(unset)} 条冻结条目尚未筛选",
                    "detail": "literature_screening_required_for_export",
                    "evidence": [str(u.get("id")) for u in unset[:8]],
                    "location": {},
                }
            )
    return findings
