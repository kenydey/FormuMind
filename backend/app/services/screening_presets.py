"""Screening rule presets (W6-2): chemistry-domain starting criteria.

Pure data — no I/O, no settings. Each preset is a ready-made ``criteria``
dict for :func:`literature_screening.screen_project`. Explicit criteria
passed by the caller always override preset values key by key.
"""
from __future__ import annotations

from typing import Any

_PRESET_CRITERIA_KEYS = (
    "include_keywords",
    "exclude_keywords",
    "require_doi",
    "year_min",
    "year_max",
)


def _criteria(
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    require_doi: bool = False,
    year_min: int | None = None,
    year_max: int | None = None,
) -> dict[str, Any]:
    return {
        "include_keywords": list(include or []),
        "exclude_keywords": list(exclude or []),
        "require_doi": bool(require_doi),
        "year_min": year_min,
        "year_max": year_max,
    }


PRESETS: dict[str, dict[str, Any]] = {
    "anticorrosion_coating": {
        "title": "防腐涂料",
        "description": "环氧 / 聚氨酯 / 富锌等防腐涂层体系文献",
        "criteria": _criteria(
            include=[
                "coating",
                "corrosion",
                "epoxy",
                "polyurethane",
                "zinc-rich",
                "anticorrosive",
                "防腐",
                "涂料",
                "环氧",
            ],
            exclude=["food", "drug", "cosmetic", "食品", "药物", "化妆品"],
        ),
    },
    "metal_pretreatment": {
        "title": "金属表面处理",
        "description": "脱脂 / 抛丸 / 磷化等前处理工艺文献",
        "criteria": _criteria(
            include=[
                "pretreatment",
                "phosphating",
                "degreasing",
                "conversion coating",
                "blasting",
                "pickling",
                "表面处理",
                "前处理",
                "磷化",
                "脱脂",
            ],
            exclude=["food", "drug", "食品", "药物"],
        ),
    },
    "conversion_coating": {
        "title": "转化膜",
        "description": "锆 / 钛 / 硅烷等无铬转化膜文献（含 VIANT 相关）",
        "criteria": _criteria(
            include=[
                "conversion coating",
                "zirconium",
                "titanium",
                "silane",
                "chromate-free",
                "passivation",
                "转化膜",
                "锆",
                "钝化",
            ],
            exclude=["food", "drug", "食品", "药物"],
        ),
    },
    "waterborne_coating": {
        "title": "水性涂料",
        "description": "水性单涂层 / 低 VOC 涂料体系文献",
        "criteria": _criteria(
            include=[
                "waterborne",
                "water-based",
                "aqueous",
                "low VOC",
                "水性",
                "单涂层",
            ],
            exclude=["solvent-based", "food", "drug", "溶剂型", "食品"],
        ),
    },
    "patents_only": {
        "title": "仅专利",
        "description": "只保留标题/摘要提及专利的条目（关键词启发式）",
        "criteria": _criteria(include=["patent", "专利"]),
    },
    "exclude_patents": {
        "title": "排除专利",
        "description": "剔除标题/摘要提及专利的条目，聚焦期刊与会议文献",
        "criteria": _criteria(exclude=["patent", "专利"]),
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Return ``[{name, title, description, criteria}]`` in stable order."""
    return [
        {
            "name": name,
            "title": p["title"],
            "description": p["description"],
            "criteria": {k: p["criteria"][k] for k in _PRESET_CRITERIA_KEYS},
        }
        for name, p in PRESETS.items()
    ]


def get_preset(name: str | None) -> dict[str, Any] | None:
    """Return the preset's criteria dict (a copy), or None for unknown names."""
    if not name:
        return None
    preset = PRESETS.get(name)
    if preset is None:
        return None
    return {k: preset["criteria"][k] for k in _PRESET_CRITERIA_KEYS}


def merge_preset(preset_name: str | None, criteria: dict[str, Any]) -> dict[str, Any]:
    """Merge preset criteria with explicit criteria; explicit non-empty wins.

    Raises ``ValueError`` for unknown preset names so callers fail loudly
    instead of silently screening with an empty rule.
    """
    if not preset_name:
        return dict(criteria or {})
    base = get_preset(preset_name)
    if base is None:
        raise ValueError(f"unknown screening preset: {preset_name!r}")
    merged = dict(base)
    for key in _PRESET_CRITERIA_KEYS:
        if key not in (criteria or {}):
            continue
        val = criteria[key]
        # Empty include/exclude lists or None thresholds do not override;
        # explicit False for require_doi does (it is a real choice).
        if key in ("include_keywords", "exclude_keywords"):
            if val:
                merged[key] = list(val)
        elif key == "require_doi":
            merged[key] = bool(val)
        else:  # year_min / year_max
            if val is not None:
                merged[key] = val
    # Carry through non-criteria extras (e.g. force) untouched.
    for key, val in (criteria or {}).items():
        if key not in _PRESET_CRITERIA_KEYS:
            merged[key] = val
    return merged
