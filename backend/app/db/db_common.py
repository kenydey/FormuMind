"""Shared primitives for DB store modules.

Currently hosts the LIKE-pattern escaper duplicated (character-identical)
across ``entity_store``, ``material_store`` and ``product_store`` — extracted
during the code-review deduplication pass 2026-09-28.

All three stores use ``escape="\\\\"`` style SQL LIKE escaping, so one shared
implementation is safe for every call site.
"""

from __future__ import annotations


def escape_like(term: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so ``term`` is matched literally in LIKE patterns."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
