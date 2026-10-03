"""Role inference for ingredient names (substring rules → ``resin`` / ``hardener`` / …).

Despite the module name, nothing here links formulations to knowledge-graph entities:
that linker was never finished. ``db.models.KGFormulationLink`` (table
``kg_formulation_links``) is still declared but nothing writes or reads it — see the
round-3 audit plan before building on it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _infer_role(name: str) -> str:
    """子串 → Role 推断。规则表见 ``resources/rules/linker_roles.toml``
    (R1, 2026-09-04: 自 _ROLE_HINTS 硬编码迁移; FORMUMIND_RULES_DIR 可
    覆盖, 缺失回退内置默认)。遍历顺序即优先级(TOML 保序)。"""
    from ..rule_loader import load_rules

    name_lower = name.lower()
    hints = load_rules("linker_roles")["role_hints"]
    for role, role_hints in hints.items():
        for hint in role_hints:
            if hint in name_lower:
                return role
    return "unknown"
