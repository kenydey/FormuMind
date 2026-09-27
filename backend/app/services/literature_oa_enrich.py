"""Wave D — batch OA fulltext enrich into literature manifest (fail-open)."""
from __future__ import annotations

import logging
import time
from typing import Any, Literal

from ..domain.schemas import Evidence
from . import literature_manifest as lm
from .fulltext_fetcher import classify, enrich_search_results

logger = logging.getLogger(__name__)

Scope = Literal["candidates", "frozen", "missing_fulltext"]


def oa_enrich_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "literature_oa_enrich_enabled", True))


def _item_needs_fulltext(item: dict[str, Any]) -> bool:
    if item.get("has_fulltext"):
        return False
    if item.get("enrich_status") == "fetched":
        return False
    doi = (item.get("doi") or "").strip()
    oa = (item.get("oa_pdf_url") or "").strip()
    return bool(doi or oa)


def _item_to_evidence(item: dict[str, Any]) -> Evidence:
    doi = (str(item.get("doi") or "").strip() or None)
    title = str(item.get("title") or item.get("id") or "untitled")[:240]
    # fulltext_fetcher.classify detects literature via DOI/arXiv in identifier.
    ident = doi or str(item.get("id") or title)
    return Evidence(
        source=str(item.get("source") or "literature"),
        identifier=ident,
        title=title,
        snippet=str(item.get("snippet") or "")[:800],
        relevance=0.5,
        oa_pdf_url=item.get("oa_pdf_url"),
        is_oa=True if item.get("oa_pdf_url") else None,
        url=item.get("url"),
    )


def enrich_manifest_oa(
    project_id: str,
    *,
    scope: Scope = "missing_fulltext",
    limit: int = 20,
    actor: str = "user",
    settings: Any = None,
) -> dict[str, Any]:
    """Fetch OA fulltext for manifest items; persist into KB; update items."""
    from ..config import get_settings

    settings = settings or get_settings()
    if not lm.manifest_enabled(settings):
        raise PermissionError("literature_manifest_enabled is false")
    if not oa_enrich_enabled(settings):
        raise PermissionError("literature_oa_enrich_enabled is false")

    limit = max(1, min(int(limit or 20), 40))
    man = lm.load_manifest(project_id)
    items = list(man.get("items") or [])
    frozen_ids = set((man.get("frozen") or {}).get("item_ids") or [])

    selected: list[dict[str, Any]] = []
    for item in items:
        iid = str(item.get("id") or "")
        if scope == "frozen" and iid not in frozen_ids:
            continue
        if not _item_needs_fulltext(item):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break

    attempted = len(selected)
    fetched = 0
    persisted = 0
    skipped = 0
    failures: list[dict[str, Any]] = []

    if not selected:
        return {
            "attempted": 0,
            "fetched": 0,
            "persisted": 0,
            "skipped": 0,
            "failures": [],
            "manifest": man,
        }

    by_id = {str(i.get("id")): i for i in items}

    for it in selected:
        ev = _item_to_evidence(it)
        kind = classify(ev)
        if not kind:
            skipped += 1
            it["enrich_status"] = "skipped"
            it["enriched_at"] = time.time()
            failures.append(
                {
                    "item_id": it.get("id"),
                    "doi": it.get("doi"),
                    "reason": "unfetchable",
                }
            )
            by_id[str(it.get("id"))] = it
            continue
        try:
            _out, report = enrich_search_results(
                [ev],
                max_docs=1,
                persist=True,
                project_id=project_id,
                force=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("OA enrich item failed %s: %s", it.get("id"), exc)
            skipped += 1
            it["enrich_status"] = "failed"
            it["enriched_at"] = time.time()
            failures.append(
                {
                    "item_id": it.get("id"),
                    "doi": it.get("doi"),
                    "reason": f"error:{str(exc)[:100]}",
                }
            )
            by_id[str(it.get("id"))] = it
            continue

        if report.succeeded > 0:
            fetched += 1
            persisted += 1
            it["has_fulltext"] = True
            it["enrich_status"] = "fetched"
            it["enriched_at"] = time.time()
            it["source_id"] = it.get("source_id") or it.get("id")
        else:
            skipped += 1
            it["enrich_status"] = "failed"
            it["enriched_at"] = time.time()
            failures.append(
                {
                    "item_id": it.get("id"),
                    "doi": it.get("doi"),
                    "reason": "no_oa|fetch_failed",
                }
            )
        by_id[str(it.get("id"))] = it

    man["items"] = list(by_id.values())

    if persisted and man.get("frozen"):
        man["frozen"] = None
        man["events"] = (man.get("events") or [])[-180:] + [
            {
                "type": "unfrozen",
                "at": time.time(),
                "reason": "oa_enrich",
                "actor": actor,
            }
        ]

    man["events"] = (man.get("events") or [])[-180:] + [
        {
            "type": "oa_enrich",
            "at": time.time(),
            "actor": actor,
            "attempted": attempted,
            "fetched": fetched,
            "persisted": persisted,
        }
    ]
    man = lm.save_manifest(man)

    return {
        "attempted": attempted,
        "fetched": fetched,
        "persisted": persisted,
        "skipped": skipped,
        "failures": failures,
        "manifest": man,
    }
