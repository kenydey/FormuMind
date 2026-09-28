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


def validate_bbox(bbox: object) -> list[float] | None:
    """Validate a normalized page-fraction bbox, or return None.

    Accepts ``[xmin, ymin, xmax, ymax]`` with every coordinate a 0–1 float and
    ``xmin <= xmax``, ``ymin <= ymax``. Returns the coordinates as a plain
    ``list[float]`` (JSON-safe). Raises ``ValueError`` on malformed input —
    callers should treat a bad bbox as "no geometry" only when they explicitly
    choose to, never silently.
    """
    if bbox is None:
        return None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"bbox must be [xmin, ymin, xmax, ymax], got {bbox!r}")
    try:
        coords = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise ValueError(f"bbox coordinates must be numeric, got {bbox!r}")
    xmin, ymin, xmax, ymax = coords
    if not all(0.0 <= v <= 1.0 for v in coords):
        raise ValueError(f"bbox coordinates must be 0–1 page fractions, got {bbox!r}")
    if xmin > xmax or ymin > ymax:
        raise ValueError(f"bbox min must not exceed max, got {bbox!r}")
    return coords
