"""Requirement → recommend / DOE effect tracing (post-A′ #2).

Answers: which brief fields are actually consumed by scoring / DOE / validation,
which are display-only, and which exist on the schema but are unwired.
"""
from __future__ import annotations

from typing import Any

from ..domain.project_spec import normalize_constraints, resolve_levers
from ..domain.schemas import Requirement


# status: wired | display_only | unwired
# consumers: recommend | doe | validate | process | reconstruct


def build_requirement_effect_trace(req: Requirement | None) -> list[dict[str, Any]]:
    """Return an ordered audit list for UI 「约束追踪」."""
    if req is None:
        return []

    items: list[dict[str, Any]] = []

    objectives = list(getattr(req, "objectives", None) or [])
    if objectives:
        for o in objectives:
            metric = str(getattr(o, "metric", None) or getattr(o, "name", None) or "").strip()
            if not metric:
                continue
            items.append(
                {
                    "field": f"objectives.{metric}",
                    "kind": "objective",
                    "label": metric,
                    "status": "wired",
                    "consumers": ["recommend", "doe"],
                    "detail": f"direction={getattr(o, 'direction', 'maximize')}",
                }
            )
    else:
        items.append(
            {
                "field": "objectives",
                "kind": "objective",
                "label": "(默认域目标)",
                "status": "wired",
                "consumers": ["recommend"],
                "detail": "未声明 objectives 时走域默认 primary objective",
            }
        )

    levers = list(getattr(req, "levers", None) or [])
    if levers:
        for lev in levers:
            name = str(getattr(lev, "name", None) or getattr(lev, "material", None) or "").strip()
            if not name:
                continue
            items.append(
                {
                    "field": f"levers.{name}",
                    "kind": "lever",
                    "label": name,
                    "status": "wired",
                    "consumers": ["doe"],
                    "detail": "resolve_levers → levers_to_doe_factors",
                }
            )
    else:
        # resolve_levers may still synthesize defaults — mark as wired-via-default
        try:
            resolved = resolve_levers(req) or []
        except Exception:
            resolved = []
        if resolved:
            items.append(
                {
                    "field": "levers",
                    "kind": "lever",
                    "label": f"(域默认 {len(resolved)} 个杠杆)",
                    "status": "wired",
                    "consumers": ["doe"],
                    "detail": "req.levers 空 → resolve_levers 回填",
                }
            )
        else:
            items.append(
                {
                    "field": "levers",
                    "kind": "lever",
                    "label": "levers",
                    "status": "unwired",
                    "consumers": [],
                    "detail": "无杠杆且无法合成默认",
                }
            )

    # Scalar constraints consumed by validate / process / reconstruct heuristics
    scalar_map = [
        ("voc_limit_gpl", "VOC 上限", ["validate", "recommend"], "wired"),
        ("cure_temperature_c", "固化温度上限", ["process", "reconstruct"], "wired"),
        ("ph_target", "pH 目标", ["display"], "display_only"),
        ("salt_spray_hours", "耐盐雾目标", ["recommend"], "wired"),
        ("film_weight_gsm", "干膜重目标", ["display"], "display_only"),
        ("cleaning_efficiency", "清洗率目标", ["recommend"], "wired"),
    ]
    for attr, label, consumers, status in scalar_map:
        val = getattr(req, attr, None)
        if val is None:
            continue
        if attr in ("salt_spray_hours", "film_weight_gsm", "cleaning_efficiency") and not val:
            continue
        # When an explicit objective covers the same metric, scalar is secondary
        cons = list(consumers)
        if status == "display_only":
            cons = []
        items.append(
            {
                "field": attr,
                "kind": "constraint",
                "label": f"{label}={val}",
                "status": status,
                "consumers": cons,
                "detail": "legacy scalar on Requirement",
            }
        )

    cv = dict(getattr(req, "constraint_values", None) or {})
    for key, raw in cv.items():
        if raw is None:
            continue
        items.append(
            {
                "field": f"constraint_values.{key}",
                "kind": "constraint",
                "label": f"{key}={raw}",
                "status": "wired",
                "consumers": ["recommend", "doe"],
                "detail": "normalize_constraints SSOT",
            }
        )

    # Notes / materials list — display unless empty
    if (getattr(req, "notes", None) or "").strip():
        items.append(
            {
                "field": "notes",
                "kind": "meta",
                "label": "notes",
                "status": "display_only",
                "consumers": [],
                "detail": "进入 LLM prompt，不进数值评分",
            }
        )
    mats = list(getattr(req, "materials", None) or [])
    if mats:
        items.append(
            {
                "field": "materials",
                "kind": "meta",
                "label": f"materials×{len(mats)}",
                "status": "wired",
                "consumers": ["reconstruct"],
                "detail": "材料偏好进入 genome/reconstruct",
            }
        )

    # Ensure normalize_constraints is exercised for side-effect documentation
    try:
        _ = normalize_constraints(req)
    except Exception:
        pass

    return items


def effect_trace_summary(trace: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"wired": 0, "display_only": 0, "unwired": 0}
    for it in trace:
        st = str(it.get("status") or "")
        if st in counts:
            counts[st] += 1
    return counts
