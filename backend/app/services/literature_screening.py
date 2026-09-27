"""Lightweight literature smart screening (Wave B / AIPOCH subset)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from . import literature_manifest as lm

logger = logging.getLogger(__name__)

# P0-4：输入证据不足（缺 title 或缺 snippet/abstract）时的 decision 模型标记
INSUFFICIENT_EVIDENCE_MODEL = "insufficient-evidence"


def screening_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "literature_screening_enabled", False))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _screening_input_digest(item: dict[str, Any]) -> str:
    """P0-7：单条待筛输入的稳定指纹 sha256(title|snippet|doi)。"""
    parts = [
        str(item.get("title") or ""),
        str(item.get("snippet") or item.get("abstract") or ""),
        str(item.get("doi") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _screening_rule_version(criteria: dict[str, Any]) -> str:
    """P0-7：筛选规则版本的稳定指纹（规则变了就重筛）。"""
    payload = {
        "include": sorted(criteria.get("include_keywords") or []),
        "exclude": sorted(criteria.get("exclude_keywords") or []),
        "require_doi": bool(criteria.get("require_doi") or False),
        "year_min": criteria.get("year_min"),
        "year_max": criteria.get("year_max"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _heuristic_classify(
    item: dict[str, Any],
    *,
    include_keywords: list[str],
    exclude_keywords: list[str],
    require_doi: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Heuristic match | no_match | uncertain（不含短路逻辑）。"""
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


def _classify_with_model(
    item: dict[str, Any],
    *,
    include_keywords: list[str],
    exclude_keywords: list[str],
    require_doi: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
) -> tuple[str, str | None]:
    """返回 (disposition, model)。

    P0-4 短路：无 title 或无 snippet/abstract → "uncertain"，
    避免用垃圾输入烧 token，decision 标 insufficient-evidence。
    """
    if not (item.get("title") or "").strip():
        return "uncertain", INSUFFICIENT_EVIDENCE_MODEL
    if not str(item.get("snippet") or item.get("abstract") or "").strip():
        return "uncertain", INSUFFICIENT_EVIDENCE_MODEL
    return (
        _heuristic_classify(
            item,
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            require_doi=require_doi,
            year_min=year_min,
            year_max=year_max,
        ),
        None,
    )


def classify_item(
    item: dict[str, Any],
    *,
    include_keywords: list[str],
    exclude_keywords: list[str],
    require_doi: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Heuristic match | no_match | uncertain（旧签名，含 P0-4 短路）。"""
    disp, _ = _classify_with_model(
        item,
        include_keywords=include_keywords,
        exclude_keywords=exclude_keywords,
        require_doi=require_doi,
        year_min=year_min,
        year_max=year_max,
    )
    return disp


def screen_project(
    project_id: str,
    criteria: dict[str, Any],
    *,
    apply: bool = True,
    settings: Any = None,
) -> dict[str, Any]:
    """Run heuristic screening; optionally persist dispositions + auto-freeze.

    P0-7：输入 digest 与规则版本都未变的条目跳过重筛（decision 标
    skipped_digest，沿用旧值）；P0-8：screening_source=="human" 的条目受
    保护，criteria 显式 force=True 时除外。
    """
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
    force = bool(criteria.get("force") or False)
    rule_version = _screening_rule_version(criteria)
    now = time.time()
    decisions: list[dict[str, Any]] = []
    for item in items:
        # P0-8：人工判定受保护，force=True 时除外
        if not force and item.get("screening_source") == "human":
            decisions.append(
                {
                    "id": item.get("id"),
                    "screening": item.get("screening") or "unset",
                    "title": item.get("title"),
                    "skipped_human": True,
                }
            )
            continue
        digest = _screening_input_digest(item)
        # P0-7：输入与规则都未变 → 跳过，沿用旧 screening
        if (
            not force
            and item.get("screening_input_digest") == digest
            and item.get("screening_rule_version") == rule_version
        ):
            decisions.append(
                {
                    "id": item.get("id"),
                    "screening": item.get("screening") or "unset",
                    "title": item.get("title"),
                    "skipped_digest": True,
                }
            )
            continue
        disp, model = _classify_with_model(
            item,
            include_keywords=include,
            exclude_keywords=exclude,
            require_doi=require_doi,
            year_min=int(year_min) if year_min is not None else None,
            year_max=int(year_max) if year_max is not None else None,
        )
        decision: dict[str, Any] = {
            "id": item.get("id"),
            "screening": disp,
            "title": item.get("title"),
        }
        if model:
            decision["model"] = model
        decisions.append(decision)
        if apply:
            item["screening"] = disp
            item["screening_input_digest"] = digest
            item["screening_rule_version"] = rule_version
            item["screening_source"] = "heuristic"
            item["screening_by"] = None
            item["screening_at"] = now
    if apply:
        man["items"] = items
        # P0-7：规则版本落盘到 manifest 顶层
        man["screening_rule_version"] = rule_version
        if man.get("frozen"):
            man["frozen"] = None
        man["events"] = (man.get("events") or [])[-180:] + [
            {
                "type": "screened",
                "at": now,
                "count": len(decisions),
                "criteria": {
                    "include_keywords": include,
                    "exclude_keywords": exclude,
                    "require_doi": require_doi,
                    "rule_version": rule_version[:16],
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
