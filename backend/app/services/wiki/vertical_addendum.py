"""Optional vertical prompt addenda for project dossier narrative (P4).

Default: no addendum. Set ``FORMUMIND_WIKI_DOSSIER_VERTICAL_ADDENDUM=silane``
(or pass ``vertical=``) to append domain-specific compiler guidance. Never
changes deterministic tables — only LLM narrative instructions.
"""
from __future__ import annotations

from typing import Callable

# id → callable returning extra system-prompt text
_ADDENDA: dict[str, Callable[[], str]] = {}


def register_addendum(addendum_id: str, factory: Callable[[], str]) -> None:
    key = (addendum_id or "").strip().lower()
    if not key:
        raise ValueError("addendum_id required")
    _ADDENDA[key] = factory


def list_addenda() -> list[str]:
    return sorted(_ADDENDA)


def resolve_addendum(addendum_id: str | None = None) -> str:
    """Return addendum text or empty string when unset / unknown."""
    from ...config import get_settings

    raw = (addendum_id if addendum_id is not None else get_settings().wiki_dossier_vertical_addendum) or ""
    key = raw.strip().lower()
    if not key:
        return ""
    factory = _ADDENDA.get(key)
    if factory is None:
        return ""
    try:
        return (factory() or "").strip()
    except Exception:
        return ""


def _silane_addendum() -> str:
    return (
        "垂直附加（硅烷 / 转化膜 / 表面处理）：\n"
        "- 强调水解–缩合、pH/浊点、游离酸/总酸、金属离子累积与槽液维护。\n"
        "- 配方比例单位必须显式（Wt% vs g/L）；不得混淆浴液浓度与膜重。\n"
        "- 提及偶联剂时尽量链到已有 L1 chemicals/materials 页。\n"
        "- 不得编造未出现在 ContextPack 中的 ASTM 数据或 SEM 图路径。"
    )


register_addendum("silane", _silane_addendum)
register_addendum("silane_conversion", _silane_addendum)
