"""Lightweight literature smart screening (Wave B / AIPOCH subset)."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Callable

from . import literature_manifest as lm
from . import screening_presets as _presets

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
    preset: str | None = None,
    rule_name: str | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Run heuristic screening; optionally persist dispositions + auto-freeze.

    P0-7：输入 digest 与规则版本都未变的条目跳过重筛（decision 标
    skipped_digest，沿用旧值）；P0-8：screening_source=="human" 的条目受
    保护，criteria 显式 force=True 时除外。

    W6-2：``preset`` 为预设名（见 screening_presets），与显式 criteria 按
    key 合并（显式非空值优先）；``rule_name`` 为本次规则命名版本；大
    manifest 场景下调用方可传 ``progress_cb(done, total)`` 报告进度。
    """
    if settings is not None and not screening_enabled(settings):
        raise PermissionError("literature_screening_enabled is false")
    criteria = _presets.merge_preset(preset, criteria or {})
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
    total = len(items)
    decisions: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
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
            if progress_cb is not None:
                progress_cb(idx + 1, total)
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
            if progress_cb is not None:
                progress_cb(idx + 1, total)
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
            if rule_name:
                item["screening_rule_name"] = rule_name
            item["screening_source"] = "heuristic"
            item["screening_by"] = None
            item["screening_at"] = now
        if progress_cb is not None:
            progress_cb(idx + 1, total)
    if apply:
        man["items"] = items
        # P0-7：规则版本落盘到 manifest 顶层
        man["screening_rule_version"] = rule_version
        # W6-2：合并后 criteria 与命名版本一并落盘，供 evaluate 默认复用
        man["screening_criteria"] = {
            "include_keywords": include,
            "exclude_keywords": exclude,
            "require_doi": require_doi,
            "year_min": year_min,
            "year_max": year_max,
        }
        if rule_name:
            man["screening_rule_name"] = rule_name
        if preset:
            man["screening_preset"] = preset
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
                    "preset": preset,
                    "rule_name": rule_name,
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


# ── W6-2：命名规则版本管理 + 变更审计 ───────────────────────────────────────

# 历史记录 append-only 上限：防 manifest 无界膨胀，不做删除/修改。
_RULE_HISTORY_CAP = 500


def _rule_versions(man: dict[str, Any]) -> list[dict[str, Any]]:
    versions = man.get("screening_rule_versions")
    return list(versions) if isinstance(versions, list) else []


def _append_rule_history(man: dict[str, Any], entry: dict[str, Any]) -> None:
    history = man.get("screening_rule_history")
    history = list(history) if isinstance(history, list) else []
    history.append(entry)
    man["screening_rule_history"] = history[-_RULE_HISTORY_CAP:]


def save_rule_version(
    project_id: str,
    *,
    name: str,
    criteria: dict[str, Any],
    created_by: str = "user",
    changelog: str = "",
    settings: Any = None,
) -> dict[str, Any]:
    """Save criteria as a named rule version (append-only).

    同名 name 可保存多次（每次生成新 version hash），历史全部保留；
    ``list_rule_versions`` 按时间倒序返回。
    """
    if settings is not None and not screening_enabled(settings):
        raise PermissionError("literature_screening_enabled is false")
    name = (name or "").strip()
    if not name:
        raise ValueError("rule name is required")
    criteria = dict(criteria or {})
    version = _screening_rule_version(criteria)
    now = time.time()
    man = lm.load_manifest(project_id)
    versions = _rule_versions(man)
    record = {
        "name": name,
        "version": version,
        "criteria": {
            "include_keywords": list(criteria.get("include_keywords") or []),
            "exclude_keywords": list(criteria.get("exclude_keywords") or []),
            "require_doi": bool(criteria.get("require_doi") or False),
            "year_min": criteria.get("year_min"),
            "year_max": criteria.get("year_max"),
        },
        "created_by": (created_by or "user").strip() or "user",
        "created_at": now,
        "changelog": (changelog or "").strip(),
    }
    versions.append(record)
    man["screening_rule_versions"] = versions
    _append_rule_history(
        man,
        {
            "at": now,
            "actor": record["created_by"],
            "action": "saved",
            "name": name,
            "version": version,
            "changelog": record["changelog"],
        },
    )
    lm.save_manifest(man)
    return record


def list_rule_versions(
    project_id: str, *, settings: Any = None
) -> dict[str, Any]:
    """Return named rule versions (newest first) + append-only history."""
    if settings is not None and not screening_enabled(settings):
        raise PermissionError("literature_screening_enabled is false")
    man = lm.load_manifest(project_id)
    versions = sorted(
        _rule_versions(man), key=lambda r: r.get("created_at") or 0, reverse=True
    )
    history = man.get("screening_rule_history")
    return {
        "project_id": project_id,
        "versions": versions,
        "history": list(history) if isinstance(history, list) else [],
        "current_rule_name": man.get("screening_rule_name"),
        "current_rule_version": man.get("screening_rule_version"),
    }


def diff_rule_versions(
    old: dict[str, Any], new: dict[str, Any]
) -> dict[str, Any]:
    """Human-readable diff between two criteria dicts.

    返回新增/删除的关键词与阈值变化，供前端直接渲染。
    """
    old = old or {}
    new = new or {}

    def _kw_set(d: dict[str, Any], key: str) -> set[str]:
        return {str(k) for k in (d.get(key) or []) if str(k).strip()}

    diff: dict[str, Any] = {
        "include_added": sorted(_kw_set(new, "include_keywords") - _kw_set(old, "include_keywords")),
        "include_removed": sorted(_kw_set(old, "include_keywords") - _kw_set(new, "include_keywords")),
        "exclude_added": sorted(_kw_set(new, "exclude_keywords") - _kw_set(old, "exclude_keywords")),
        "exclude_removed": sorted(_kw_set(old, "exclude_keywords") - _kw_set(new, "exclude_keywords")),
        "threshold_changes": [],
    }
    for key in ("require_doi", "year_min", "year_max"):
        ov, nv = old.get(key), new.get(key)
        if key == "require_doi":
            ov, nv = bool(ov), bool(nv)
        if ov != nv:
            diff["threshold_changes"].append({"field": key, "old": ov, "new": nv})
    diff["changed"] = bool(
        diff["include_added"]
        or diff["include_removed"]
        or diff["exclude_added"]
        or diff["exclude_removed"]
        or diff["threshold_changes"]
    )
    return diff


def rollback_rule_version(
    project_id: str,
    *,
    version: str | None = None,
    name: str | None = None,
    actor: str = "user",
    settings: Any = None,
) -> dict[str, Any]:
    """Re-run screening with a previously saved named rule version.

    ``version``（hash）优先；否则取 ``name`` 的最新版本。force=False：
    P0-8 人工保护仍然生效；P0-7 digest 跳过对"本来就是该版本筛过"的
    条目依然有效，只有规则真正变化的条目才重筛。
    """
    if settings is not None and not screening_enabled(settings):
        raise PermissionError("literature_screening_enabled is false")
    man = lm.load_manifest(project_id)
    versions = _rule_versions(man)
    target: dict[str, Any] | None = None
    if version:
        target = next((v for v in versions if v.get("version") == version), None)
    elif name:
        named = [v for v in versions if v.get("name") == name]
        target = max(named, key=lambda v: v.get("created_at") or 0) if named else None
    if target is None:
        raise LookupError("rule version not found")
    result = screen_project(
        project_id,
        dict(target.get("criteria") or {}),
        apply=True,
        settings=settings,
        rule_name=target.get("name"),
    )
    man = lm.load_manifest(project_id)
    _append_rule_history(
        man,
        {
            "at": time.time(),
            "actor": (actor or "user").strip() or "user",
            "action": "rolled_back",
            "name": target.get("name"),
            "version": target.get("version"),
            "changelog": f"rollback to {target.get('name')} @ {str(target.get('version'))[:12]}",
        },
    )
    lm.save_manifest(man)
    result["rolled_back_to"] = {
        "name": target.get("name"),
        "version": target.get("version"),
    }
    return result


# ── W6-2：规则效果评估 ─────────────────────────────────────────────────────

# 人工标注样本过少时不硬算指标，避免误导。
_EVAL_MIN_LABELED = 5


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def evaluate_screening(
    project_id: str,
    criteria: dict[str, Any] | None = None,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Evaluate rule quality on human-labeled items (screening_source=='human').

    二分类：人工标 match 为正例，no_match 为负例（uncertain 不计入）。
    预测用与 screen_project 相同的 _classify_with_model（含 P0-4 短路），
    apply=False 不写 manifest。返回混淆矩阵 + P/R/F1 + 逐关键词消融：
    include 关键词逐个移除看 recall 掉多少（最伤 recall 的排前面），
    exclude 关键词逐个移除看 precision 变化。
    """
    if settings is not None and not screening_enabled(settings):
        raise PermissionError("literature_screening_enabled is false")
    man = lm.load_manifest(project_id)
    items = list(man.get("items") or [])
    labeled = [
        i
        for i in items
        if i.get("screening_source") == "human"
        and (i.get("screening") or "") in ("match", "no_match")
    ]
    if len(labeled) < _EVAL_MIN_LABELED:
        return {
            "project_id": project_id,
            "evaluated": False,
            "reason": "insufficient_labeled",
            "labeled_count": len(labeled),
            "min_labeled": _EVAL_MIN_LABELED,
        }
    criteria = dict(criteria or man.get("screening_criteria") or {})
    include = list(criteria.get("include_keywords") or [])
    exclude = list(criteria.get("exclude_keywords") or [])
    require_doi = bool(criteria.get("require_doi") or False)
    year_min = criteria.get("year_min")
    year_max = criteria.get("year_max")

    def _predict(
        item: dict[str, Any],
        inc: list[str],
        exc: list[str],
    ) -> bool:
        disp, _ = _classify_with_model(
            item,
            include_keywords=inc,
            exclude_keywords=exc,
            require_doi=require_doi,
            year_min=int(year_min) if year_min is not None else None,
            year_max=int(year_max) if year_max is not None else None,
        )
        return disp == "match"

    def _confusion(inc: list[str], exc: list[str]) -> dict[str, int]:
        tp = fp = tn = fn = 0
        for item in labeled:
            actual_pos = (item.get("screening") or "") == "match"
            pred_pos = _predict(item, inc, exc)
            if actual_pos and pred_pos:
                tp += 1
            elif actual_pos and not pred_pos:
                fn += 1
            elif not actual_pos and pred_pos:
                fp += 1
            else:
                tn += 1
        return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}

    base_cm = _confusion(include, exclude)
    base = _prf(base_cm["tp"], base_cm["fp"], base_cm["fn"])
    # 消融：逐个移除 include 关键词 → recall 变化（最伤 recall 的优先）
    include_ablation: list[dict[str, Any]] = []
    for kw in include:
        rest = [k for k in include if k != kw]
        cm = _confusion(rest, exclude)
        m = _prf(cm["tp"], cm["fp"], cm["fn"])
        include_ablation.append(
            {
                "keyword": kw,
                "recall_without": m["recall"],
                "recall_delta": m["recall"] - base["recall"],
                "f1_without": m["f1"],
            }
        )
    include_ablation.sort(key=lambda r: r["recall_delta"])
    # 消融：逐个移除 exclude 关键词 → precision 变化
    exclude_ablation: list[dict[str, Any]] = []
    for kw in exclude:
        rest = [k for k in exclude if k != kw]
        cm = _confusion(include, rest)
        m = _prf(cm["tp"], cm["fp"], cm["fn"])
        exclude_ablation.append(
            {
                "keyword": kw,
                "precision_without": m["precision"],
                "precision_delta": m["precision"] - base["precision"],
                "f1_without": m["f1"],
            }
        )
    exclude_ablation.sort(key=lambda r: r["precision_delta"])
    return {
        "project_id": project_id,
        "evaluated": True,
        "labeled_count": len(labeled),
        "labeled_positive": sum(1 for i in labeled if i.get("screening") == "match"),
        "labeled_negative": sum(1 for i in labeled if i.get("screening") == "no_match"),
        "confusion": base_cm,
        "metrics": base,
        "include_ablation": include_ablation,
        "exclude_ablation": exclude_ablation,
        "criteria": {
            "include_keywords": include,
            "exclude_keywords": exclude,
            "require_doi": require_doi,
            "year_min": year_min,
            "year_max": year_max,
        },
    }


# ── W6-2：批量异步阈值 ─────────────────────────────────────────────────────

def screening_async_threshold(settings: Any = None) -> int:
    """Manifest 超过该条目数时走后台 job（读 settings，可测试隔离）。"""
    try:
        return int(getattr(settings, "screening_async_threshold", 500) or 500)
    except (TypeError, ValueError):
        return 500
