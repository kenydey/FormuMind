"""Unified research / literature query builder with substrate context injection."""
from __future__ import annotations

from .schemas import Requirement, Substrate

# Light-metal substrates need explicit retrieval terms — otherwise search drifts to steel/phosphate.
_PRIORITY_SUBSTRATES = frozenset({Substrate.magnesium_alloy, Substrate.aluminum})

SUBSTRATE_QUERY_TERMS: dict[Substrate, list[str]] = {
    Substrate.magnesium_alloy: [
        "magnesium alloy",
        "AZ91",
        "AM60",
        "镁合金",
        "镁材钝化",
        "镁合金转化膜",
    ],
    Substrate.aluminum: [
        "aluminum alloy",
        "铝合金",
        "铝材转化膜",
        "chrome-free conversion",
    ],
    Substrate.galvanized_steel: ["galvanized steel", "镀锌钢"],
    Substrate.stainless_steel: ["stainless steel", "不锈钢"],
    Substrate.carbon_steel: ["carbon steel", "低碳钢"],
}


def build_research_query(topic: str = "", req: Requirement | None = None) -> str:
    """Merge user topic, requirement headline, and substrate-specific search terms."""
    t = (topic or "").strip()
    if req is None:
        return t or "coating formulation"

    if not t:
        parts = [req.headline().strip()]
        if req.substrate in _PRIORITY_SUBSTRATES:
            parts.extend(SUBSTRATE_QUERY_TERMS.get(req.substrate, []))
        return " ".join(dict.fromkeys(p for p in parts if p)) or "coating formulation"

    if req.substrate in _PRIORITY_SUBSTRATES:
        parts = [t]
        t_lower = t.lower()
        for term in SUBSTRATE_QUERY_TERMS.get(req.substrate, []):
            if term.lower() not in t_lower:
                parts.append(term)
        return " ".join(dict.fromkeys(parts))

    return t
