"""Wave E-Lit — duplicate groups + merge for literature library (AIPOCH-inspired)."""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Literal

from . import literature_manifest as lm

MatchKind = Literal["identifier", "metadata"]
SCREENING_RANK = {"match": 3, "uncertain": 2, "no_match": 1, "unset": 0}


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _first_author_key(item: dict[str, Any]) -> str:
    authors = item.get("authors") or []
    if not authors:
        return ""
    first = str(authors[0] or "").strip()
    if not first:
        return ""
    # family-ish: last token or before comma
    if "," in first:
        family = first.split(",", 1)[0].strip()
    else:
        parts = first.split()
        family = parts[-1] if parts else first
    return _normalize_text(family)[:40]


def _identity_keys(item: dict[str, Any]) -> list[str]:
    lm.ensure_item_library_fields(item)
    keys: list[str] = []
    for row in item.get("identifiers") or []:
        scheme = str(row.get("scheme") or "").lower()
        value = str(row.get("value") or "").lower().strip()
        if scheme in {"doi", "chemrxiv", "openalex"} and value:
            keys.append(f"id:{scheme}:{value}")
    doi = str(item.get("doi") or "").lower().strip()
    if doi:
        keys.append(f"id:doi:{doi}")
    crx = str(item.get("chemrxiv_id") or "").lower().strip()
    if crx:
        keys.append(f"id:chemrxiv:{crx}")
    oaid = str(item.get("openalex_id") or "").strip()
    if oaid:
        keys.append(f"id:openalex:{oaid}")
    title = _normalize_text(str(item.get("title") or ""))
    year = item.get("year")
    author = _first_author_key(item)
    if title and year is not None and author:
        keys.append(f"meta:{title}|{int(year)}|{author}")
    return list(dict.fromkeys(keys))


def find_duplicate_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(items)
    if n < 2:
        return []
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a: int, b: int) -> None:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[rb] = ra

    key_to_indices: dict[str, list[int]] = {}
    for idx, item in enumerate(items):
        for key in _identity_keys(item):
            key_to_indices.setdefault(key, []).append(idx)
    for indices in key_to_indices.values():
        if len(indices) < 2:
            continue
        head = indices[0]
        for other in indices[1:]:
            join(head, other)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)

    out: list[dict[str, Any]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_items = [items[i] for i in members]
        item_ids = sorted(str(it.get("id")) for it in member_items)
        # identifier match if any shared id: key among all
        id_keys = []
        for it in member_items:
            id_keys.append({k for k in _identity_keys(it) if k.startswith("id:")})
        shared = set.intersection(*id_keys) if id_keys else set()
        match: MatchKind = "identifier" if shared else "metadata"
        title = str(member_items[0].get("title") or item_ids[0])[:200]
        out.append(
            {
                "id": item_ids[0],
                "title": title,
                "item_ids": item_ids,
                "match": match,
            }
        )
    out.sort(key=lambda g: g["id"])
    return out


def list_duplicates(project_id: str, *, settings: Any = None) -> dict[str, Any]:
    if settings is not None and not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not lm.library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    man = lm.load_manifest(project_id)
    groups = find_duplicate_groups(list(man.get("items") or []))
    return {"project_id": project_id, "groups": groups}


def _completeness(item: dict[str, Any]) -> int:
    score = 0
    for key in (
        "title",
        "doi",
        "authors",
        "year",
        "url",
        "oa_pdf_url",
        "snippet",
        "chemrxiv_id",
        "openalex_id",
        "notes",
    ):
        val = item.get(key)
        if val is None or val == "" or val == []:
            continue
        score += 1
    if item.get("has_fulltext"):
        score += 2
    score += min(len(item.get("tags") or []), 5)
    return score


def _merge_screening(values: list[str]) -> str:
    best = "unset"
    best_rank = -1
    for v in values:
        r = SCREENING_RANK.get(v or "unset", 0)
        if r > best_rank:
            best_rank = r
            best = v or "unset"
    return best


def merge_items(
    project_id: str,
    item_ids: list[str],
    *,
    strategy: str = "most-complete",
    settings: Any = None,
) -> dict[str, Any]:
    if settings is not None and not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if settings is not None and not lm.library_enabled(settings):
        raise PermissionError("literature_library_enabled is false")
    if strategy != "most-complete":
        raise ValueError("only strategy=most-complete is supported")
    ids = [str(x) for x in item_ids if str(x).strip()]
    if len(ids) < 2 or len(ids) > 20:
        raise ValueError("merge requires 2–20 item_ids")

    man = lm.load_manifest(project_id)
    by_id = {str(i.get("id")): lm.ensure_item_library_fields(i) for i in (man.get("items") or [])}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise LookupError(f"items not found: {', '.join(missing[:5])}")

    views = [by_id[i] for i in ids]
    # Require shared strong identifier for merge safety
    id_key_sets = [{k for k in _identity_keys(it) if k.startswith("id:")} for it in views]
    shared = set.intersection(*id_key_sets) if id_key_sets else set()
    if not shared:
        # allow metadata-only groups that find_duplicate_groups already clustered
        meta_sets = [{k for k in _identity_keys(it) if k.startswith("meta:")} for it in views]
        shared_meta = set.intersection(*meta_sets) if meta_sets else set()
        if not shared_meta:
            raise ValueError("items do not share a mergeable identity")

    ordered = sorted(
        views,
        key=lambda it: (-_completeness(it), str(it.get("created_at") or 0), str(it.get("id"))),
    )
    survivor = dict(ordered[0])
    survivor_id = str(survivor.get("id"))
    donors = ordered[1:]

    def fill(key: str) -> None:
        if survivor.get(key) in (None, "", []):
            for d in donors:
                if d.get(key) not in (None, "", []):
                    survivor[key] = d.get(key)
                    break

    for key in (
        "title",
        "doi",
        "chemrxiv_id",
        "openalex_id",
        "year",
        "url",
        "oa_pdf_url",
        "snippet",
        "source",
        "has_fulltext",
        "enrich_status",
    ):
        fill(key)

    authors: list[str] = []
    for it in [survivor, *donors]:
        for a in it.get("authors") or []:
            s = str(a).strip()
            if s and s not in authors:
                authors.append(s)
    survivor["authors"] = authors[:40]

    tags: list[str] = []
    for it in [survivor, *donors]:
        for t in it.get("tags") or []:
            nt = lm.normalize_tag(str(t))
            if nt and nt not in tags:
                tags.append(nt)
    survivor["tags"] = tags[:32]

    notes_parts = [str(it.get("notes") or "").strip() for it in [survivor, *donors]]
    survivor["notes"] = "\n---\n".join(p for p in notes_parts if p)[:4000]

    cids: list[str] = []
    for it in [survivor, *donors]:
        for c in it.get("collection_ids") or []:
            cs = str(c)
            if cs and cs not in cids:
                cids.append(cs)
    survivor["collection_ids"] = cids

    survivor["screening"] = _merge_screening(
        [str(it.get("screening") or "unset") for it in [survivor, *donors]]
    )
    lm.ensure_item_library_fields(survivor)
    survivor["updated_at"] = time.time()

    drop_ids = {str(it.get("id")) for it in donors}
    new_items = []
    for it in man.get("items") or []:
        iid = str(it.get("id"))
        if iid == survivor_id:
            new_items.append(survivor)
        elif iid in drop_ids:
            continue
        else:
            new_items.append(it)
    man["items"] = new_items

    # Remap frozen
    frozen = man.get("frozen")
    if frozen and isinstance(frozen, dict):
        fids = [str(x) for x in (frozen.get("item_ids") or [])]
        remapped = []
        for fid in fids:
            if fid in drop_ids:
                if survivor_id not in remapped:
                    remapped.append(survivor_id)
            elif fid not in remapped:
                remapped.append(fid)
        if remapped:
            frozen["item_ids"] = sorted(set(remapped))
            frozen["digest"] = lm.compute_digest(frozen["item_ids"], man["items"])
            man["frozen"] = frozen
        else:
            man["frozen"] = None

    # Remap collections
    for coll in man.get("collections") or []:
        members = []
        for mid in coll.get("item_ids") or []:
            mid_s = str(mid)
            if mid_s in drop_ids:
                if survivor_id not in members:
                    members.append(survivor_id)
            elif mid_s not in members:
                members.append(mid_s)
        coll["item_ids"] = members
        coll["updated_at"] = time.time()

    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "merged",
            "at": time.time(),
            "survivor_id": survivor_id,
            "merged_ids": sorted(drop_ids),
            "strategy": strategy,
        }
    ]
    man = lm.save_manifest(man)
    return {
        "project_id": project_id,
        "survivor_id": survivor_id,
        "merged_ids": sorted(drop_ids),
        "manifest": man,
    }
