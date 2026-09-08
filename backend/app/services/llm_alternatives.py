"""L4 rule / LLM functional alternatives for material substitution.

Advisory only — no formula Δ and no invented high-confidence CAS.
Failures degrade to an empty list.
"""
from __future__ import annotations

import os
import re
from typing import Any

from loguru import logger

from ..db.material_store import norm_key
from ..domain.knowledge import RAW_MATERIALS

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def substitute_llm_enabled() -> bool:
    try:
        from ..config import get_settings

        return bool(get_settings().substitute_llm)
    except Exception:
        raw = (os.environ.get("FORMUMIND_SUBSTITUTE_LLM") or "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}


def _match_catalog(name: str) -> tuple[bool, str | None]:
    key = norm_key(name or "")
    if not key:
        return False, None
    for cat_name in RAW_MATERIALS:
        if norm_key(cat_name) == key:
            return True, cat_name
    return False, None


def _kind_for_role(role: str | None) -> str:
    r = (role or "").strip().lower()
    if r in {"hardener", "crosslinker"}:
        return "substitute_crosslinker"
    if r == "resin":
        return "substitute_resin"
    if r in {"accelerator", "catalyst"}:
        return "swap_catalyst"
    if r == "inhibitor":
        return "substitute_inhibitor"
    if r == "solvent":
        return "substitute_solvent"
    return "substitute_functional"


def _from_rules(
    *,
    material: str,
    role_hint: str | None,
    limit: int,
    exclude: set[str],
) -> list[dict[str, Any]]:
    from ..agents.rules import WATERBORNE_ALTERNATIVES

    role = (role_hint or "").strip().lower()
    names = list(WATERBORNE_ALTERNATIVES.get(role) or [])
    # Role-agnostic seeds when tables miss the role: same-role catalog names
    # that share a coarse functional class keyword.
    if not names and role:
        for cat_name, spec in RAW_MATERIALS.items():
            if str(spec.get("role") or "").lower() != role:
                continue
            if norm_key(cat_name) == norm_key(material):
                continue
            names.append(cat_name)
            if len(names) >= limit * 2:
                break

    rows: list[dict[str, Any]] = []
    for name in names:
        if not name or norm_key(name) in exclude:
            continue
        if norm_key(name) == norm_key(material):
            continue
        in_cat, cat_name = _match_catalog(name)
        rows.append(
            {
                "name": cat_name or name,
                "kind": _kind_for_role(role),
                "rationale": f"规则表/同角色功能扩召回（role={role or 'unknown'}）",
                "source": "chemist_rules",
                "cas_no": None,
                "smiles": None,
                "role_hint": role_hint,
                "in_catalog": in_cat,
                "catalog_name": cat_name,
                "note": "AI/规则建议；未做配方 Δ",
            }
        )
        exclude.add(norm_key(name))
        if len(rows) >= limit:
            break
    return rows


def _from_llm(
    *,
    material: str,
    role_hint: str | None,
    limit: int,
    exclude: set[str],
) -> tuple[list[dict[str, Any]], str | None]:
    if not substitute_llm_enabled():
        return [], "FORMUMIND_SUBSTITUTE_LLM=false"
    try:
        from .llm import complete_json
    except Exception as exc:
        return [], f"llm_import_failed:{exc}"

    prompt = (
        "You suggest functional substitute materials for formulation R&D.\n"
        f"Current material: {material}\n"
        f"Role hint: {role_hint or 'unknown'}\n"
        "Return JSON only: {\"suggestions\":[{\"name\":\"...\",\"rationale\":\"...\",\"kind\":\"substitute_functional\"}]}\n"
        f"At most {limit} suggestions. Use real chemical / trade names.\n"
        "Do NOT invent CAS numbers. Do NOT invent SMILES.\n"
        "Prefer waterborne-compatible options when role is hardener/resin/accelerator.\n"
    )
    try:
        data = complete_json(prompt)
    except Exception as exc:
        logger.debug("llm substitutes failed ({})", exc)
        return [], f"llm_error:{exc}"

    if not data:
        return [], "llm_unavailable_or_invalid_json"

    suggestions = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(suggestions, list):
        return [], "llm_missing_suggestions"

    rows: list[dict[str, Any]] = []
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or norm_key(name) in exclude:
            continue
        if norm_key(name) == norm_key(material):
            continue
        # Strip any hallucinated CAS — identity CAS only from catalog/lookup.
        in_cat, cat_name = _match_catalog(name)
        rows.append(
            {
                "name": cat_name or name,
                "kind": str(item.get("kind") or _kind_for_role(role_hint)),
                "rationale": str(item.get("rationale") or "LLM 功能扩召回")[:400],
                "source": "llm",
                "cas_no": None,
                "smiles": None,
                "role_hint": role_hint,
                "in_catalog": in_cat,
                "catalog_name": cat_name,
                "note": "AI/规则建议；未做配方 Δ",
            }
        )
        exclude.add(norm_key(name))
        if len(rows) >= limit:
            break
    return rows, None


def fetch_llm_alternatives(
    *,
    material: str,
    role_hint: str | None = None,
    limit: int = 5,
    known_names: list[str] | None = None,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Aggregate chemist rules (+ optional LLM) into advisory llm substitutes."""
    lim = max(1, min(15, int(limit)))
    display = (material or "").strip()
    if not display:
        return {
            "llm": [],
            "llm_meta": {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": "empty_material",
                "mode": "off",
            },
        }

    exclude = {norm_key(n) for n in (known_names or []) if n}
    exclude.add(norm_key(display))
    providers: list[str] = []
    reasons: list[str] = []

    merged = _from_rules(
        material=display, role_hint=role_hint, limit=lim, exclude=exclude
    )
    if merged:
        providers.append("chemist_rules")

    need = lim - len(merged)
    if allow_llm and need > 0:
        llm_rows, llm_reason = _from_llm(
            material=display,
            role_hint=role_hint,
            limit=need,
            exclude=exclude,
        )
        if llm_reason:
            reasons.append(llm_reason)
        elif llm_rows:
            providers.append("llm")
            merged.extend(llm_rows)

    skipped = None
    if not merged and reasons:
        skipped = "; ".join(reasons[:3])
    elif not merged:
        skipped = "无规则/LLM 功能替代命中"

    return {
        "llm": merged[:lim],
        "llm_meta": {
            "enabled": True,
            "queried": True,
            "count": len(merged[:lim]),
            "skipped_reason": skipped,
            "providers": providers,
        },
    }
