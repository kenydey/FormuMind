"""Substrate-aware DOE lever defaults — SSOT for per-substrate optimization factors."""
from __future__ import annotations

from .schemas import LeverSpec, ProductDomain, Requirement, Substrate

# Surface-treatment levers for light metals: concentration (g/L) + process parameters.
_LIGHT_METAL_ST_LEVERS: list[tuple[str, float, float, str]] = [
    ("Hexafluorozirconic acid", 0.5, 3.0, "g/L"),
    ("(3-Aminopropyl)triethoxysilane (APTES)", 0.2, 2.0, "g/L"),
    ("Cerium nitrate", 0.1, 1.5, "g/L"),
    ("immersion_time_min", 30.0, 300.0, "min"),
    ("bath_temperature_c", 20.0, 70.0, "C"),
]

_STEEL_ST_LEVERS: list[tuple[str, float, float, str]] = [
    ("Phosphoric acid", 3.0, 14.0, "wt%"),
    ("Manganese dihydrogen phosphate", 1.0, 8.0, "wt%"),
]

SUBSTRATE_SURFACE_TREATMENT_LEVERS: dict[Substrate, list[tuple[str, float, float, str]]] = {
    Substrate.magnesium_alloy: _LIGHT_METAL_ST_LEVERS,
    Substrate.aluminum: _LIGHT_METAL_ST_LEVERS,
    Substrate.carbon_steel: _STEEL_ST_LEVERS,
    Substrate.galvanized_steel: _STEEL_ST_LEVERS,
    Substrate.stainless_steel: _STEEL_ST_LEVERS,
}

_PROCESS_LEVER_NAMES = frozenset({"immersion_time_min", "bath_temperature_c", "cure_temperature_c"})


def substrate_default_levers(req: Requirement) -> list[LeverSpec] | None:
    """Return substrate-specific DOE levers when defined for this domain."""
    if req.domain != ProductDomain.surface_treatment:
        return None
    raw = SUBSTRATE_SURFACE_TREATMENT_LEVERS.get(req.substrate)
    if not raw:
        return None
    return [LeverSpec(name=n, low=lo, high=hi, unit=u) for n, lo, hi, u in raw]


def is_process_lever(name: str) -> bool:
    return name in _PROCESS_LEVER_NAMES
