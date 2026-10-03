"""Role inference for ingredient names (substring rules → ``resin`` / ``hardener`` / …).

This module used to be called ``formulation_linker`` and sat next to a ``kg_formulation_links``
table, which suggested that formulations were linked to knowledge-graph entities. They never
were: the linker was never finished, the table was never written or read (dropped in
migration 0044), and only this function was ever used. The rules file keeps its original name
(``linker_roles.toml``) because operators may override it through ``FORMUMIND_RULES_DIR``.
"""
from __future__ import annotations


def infer_role(name: str) -> str:
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
