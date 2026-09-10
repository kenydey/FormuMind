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

# Competing-metal / process noise when a substrate is locked on the requirement.
_ALIEN_SUBSTRATE_TERMS: dict[Substrate, tuple[str, ...]] = {
    Substrate.magnesium_alloy: (
        "aluminum alloy",
        "aluminium alloy",
        "铝合金",
        "copper alloy",
        "铜合金",
        "zinc alloy",
        "锌合金",
        "molten salt",
        "熔盐",
    ),
    Substrate.aluminum: (
        "magnesium alloy",
        "镁合金",
        "copper alloy",
        "铜合金",
        "molten salt",
        "熔盐",
    ),
    Substrate.galvanized_steel: (
        "magnesium alloy",
        "镁合金",
        "aluminum alloy",
        "铝合金",
        "molten salt",
        "熔盐",
    ),
    Substrate.stainless_steel: (
        "magnesium alloy",
        "镁合金",
        "molten salt",
        "熔盐",
    ),
    Substrate.carbon_steel: (
        "magnesium alloy",
        "镁合金",
        "aluminum alloy",
        "铝合金",
        "molten salt",
        "熔盐",
    ),
}


def wrong_substrate_hit(text: str, req: Requirement | None, *, query: str = "") -> bool:
    """True when evidence leans on a competing substrate family vs requirement.

    Soft: only fires when an alien term appears and none of the locked substrate
    anchors appear in the combined text/query (avoids killing comparative papers
    that mention both).
    """
    if req is None or not getattr(req, "substrate", None):
        return False
    substrate = req.substrate
    aliens = _ALIEN_SUBSTRATE_TERMS.get(substrate) or ()
    if not aliens:
        return False
    blob = f"{text} {query}".casefold()
    our = SUBSTRATE_QUERY_TERMS.get(substrate) or []
    if any(t.casefold() in blob for t in our):
        return False
    return any(a.casefold() in blob for a in aliens)


def build_research_query(topic: str = "", req: Requirement | None = None) -> str:
    """Merge user topic, requirement headline, and substrate-specific search terms."""
    t = (topic or "").strip()
    if req is None:
        return t or "coating formulation"

    if not t:
        parts = [req.headline().strip()]
        terms = SUBSTRATE_QUERY_TERMS.get(req.substrate, [])
        if req.substrate in _PRIORITY_SUBSTRATES or terms:
            parts.extend(terms)
        return " ".join(dict.fromkeys(p for p in parts if p)) or "coating formulation"

    # Harder substrate anchor: inject for any locked substrate, not only Mg/Al.
    terms = SUBSTRATE_QUERY_TERMS.get(req.substrate, [])
    if terms:
        parts = [t]
        t_lower = t.lower()
        for term in terms:
            if term.lower() not in t_lower:
                parts.append(term)
        return " ".join(dict.fromkeys(parts))

    return t
