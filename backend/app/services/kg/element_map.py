"""Load element → chemical/trade expansion map."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_cache: dict | None = None


def load_element_map(path: str) -> dict:
    global _cache
    if _cache is not None:
        return _cache
    p = Path(path)
    if not p.is_file():
        p = Path(__file__).resolve().parents[1] / "resources" / "kg_elements.json"
    if not p.is_file():
        logger.warning("kg element map not found at %s", path)
        _cache = {}
        return _cache
    _cache = json.loads(p.read_text(encoding="utf-8"))
    return _cache
