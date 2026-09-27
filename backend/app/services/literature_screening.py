"""Lightweight literature smart screening (Wave B / AIPOCH subset)."""
from __future__ import annotations

import logging
import re
from typing import Any

from . import literature_manifest as lm

logger = logging.getLogger(__name__)


def screening_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "literature_screening_enabled", False))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def classify_item(
    item: dict[str, Any],
    *,
    include_keywords: list[str],
    exclude_keywords: list[str],
    require_doi: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Heuristic match | no_match | uncertain."""
    blob = _norm(
        f"{item.get('title') or ''} {item.get('snippet') or ''} {item.get('doi') or ''}"
    )
    excl = [k for k in (exclude_keywords or []) if k and _norm(k) in blob]
    if excl:
        return "no_match"
    if require_doi and not item.get("doi"):
        return "uncertain"
    year = item.get("year")
    if year is not None:
        try:
            y = int(year)
            if year_min is not None and y < year_min:
                return "no_match"
            if year_max is not None and y > year_max:
                return "no_match"
        except (TypeError, ValueError):
            pass
    includes = [k for k in (include_keywords or []) if k]
    if not includes:
        return "uncertain" if require_doi else "match"
    hits = [k for k in includes if _norm(k) in blob]
    if hits:
        return "match"
    return "uncertain"


def screen_project(
    project_id: str,
    criteria: dict[str, Any],
    *,
    apply: bool = True,
    settings: Any = None,
) -> dict[str, Any]:
    """Run heuristic screening; optionally persist dispositions + auto-freeze."""
    if settings is not None and not screening_enabled(settings):
        raise PermissionError("literature_screening_enabled is false")
    man = lm.load_manifest(project_id)
    items = list(man.get("items") or [])
    if not items:
        # Capture first so there is something to screen
        man = lm.capture_from_project(project_id, settings=settings)
        items = list(man.get("items") or [])
    include = list(criteria.get("include_keywords") or [])
    exclude = list(criteria.get("exclude_keywords") or [])
    require_doi = bool(criteria.get("require_doi") or False)
    year_min = criteria.get("year_min")
    year_max = criteria.get("year_max")
    decisions: list[dict[str, Any]] = []
    for item in items:
        disp = classify_item(
            item,
            include_keywords=include,
            exclude_keywords=exclude,
            require_doi=require_doi,
            year_min=int(year_min) if year_min is not None else None,
            year_max=int(year_max) if year_max is not None else None,
        )
        decisions.append({"id": item.get("id"), "screening": disp, "title": item.get("title")})
        if apply:
            item["screening"] = disp
    if apply:
        man["items"] = items
        if man.get("frozen"):
            man["frozen"] = None
        man["events"] = (man.get("events") or [])[-180:] + [
            {
                "type": "screened",
                "at": __import__("time").time(),
                "count": len(decisions),
                "criteria": {
                    "include_keywords": include,
                    "exclude_keywords": exclude,
                    "require_doi": require_doi,
                },
            }
        ]
        man = lm.save_manifest(man)
        if settings is not None and bool(getattr(settings, "screening_auto_freeze", False)):
            match_ids = [d["id"] for d in decisions if d["screening"] == "match" and d["id"]]
            if match_ids:
                try:
                    man = lm.freeze(
                        project_id,
                        item_ids=match_ids,
                        actor="screening_auto",
                        settings=settings,
                    )
                except ValueError:
                    pass
    summary = {
        "match": sum(1 for d in decisions if d["screening"] == "match"),
        "no_match": sum(1 for d in decisions if d["screening"] == "no_match"),
        "uncertain": sum(1 for d in decisions if d["screening"] == "uncertain"),
    }
    return {
        "project_id": project_id,
        "applied": bool(apply),
        "summary": summary,
        "decisions": decisions,
        "manifest": man if apply else lm.load_manifest(project_id),
    }
