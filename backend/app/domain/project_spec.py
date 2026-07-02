"""ProjectSpec helpers — normalize free-text requirements and derive DOE levers."""
from __future__ import annotations

from .levers import substrate_default_levers
from .schemas import DOEFactor, Formulation, Ingredient, LeverSpec, ObjectiveSpec, ProductDomain, Requirement, Substrate

# Roles typically held fixed (solvent fills to 100%).
_FIXED_ROLES = frozenset({"solvent", "pigment", "filler", "additive"})
_PROCESS_KEYS = frozenset({"cure_temperature_c"})

_DOMAIN_LABELS: dict[ProductDomain, str] = {
    ProductDomain.anticorrosion_coating: "防腐蚀涂料",
    ProductDomain.degreaser: "脱脂剂",
    ProductDomain.surface_treatment: "表面处理剂",
}

_LEGACY_CONSTRAINT_LABELS: dict[str, str] = {
    "voc_limit_gpl": "VOC 上限",
    "cure_temperature_c": "固化温度上限",
    "ph_target": "pH 目标",
    "salt_spray_hours": "耐盐雾目标",
    "film_weight_gsm": "干膜重目标",
    "cleaning_efficiency": "清洗率目标",
}


def normalize_constraints(req: Requirement) -> dict[str, float]:
    """Merge legacy scalar constraint fields with ``constraint_values`` (SSOT)."""
    out: dict[str, float] = {}
    if req.voc_limit_gpl is not None:
        out[_LEGACY_CONSTRAINT_LABELS["voc_limit_gpl"]] = float(req.voc_limit_gpl)
    if req.cure_temperature_c is not None:
        out[_LEGACY_CONSTRAINT_LABELS["cure_temperature_c"]] = float(req.cure_temperature_c)
    if req.ph_target is not None:
        out[_LEGACY_CONSTRAINT_LABELS["ph_target"]] = float(req.ph_target)
    if req.salt_spray_hours:
        out[_LEGACY_CONSTRAINT_LABELS["salt_spray_hours"]] = float(req.salt_spray_hours)
    if req.film_weight_gsm:
        out[_LEGACY_CONSTRAINT_LABELS["film_weight_gsm"]] = float(req.film_weight_gsm)
    if req.cleaning_efficiency:
        out[_LEGACY_CONSTRAINT_LABELS["cleaning_efficiency"]] = float(req.cleaning_efficiency)
    for key, raw in (req.constraint_values or {}).items():
        if raw is not None:
            out[key] = float(raw)
    return out


_LEGACY_LEVERS: dict[ProductDomain, list[tuple[str, float, float]]] = {
    ProductDomain.anticorrosion_coating: [
        ("Zinc phosphate", 2.0, 14.0),
        ("Bisphenol-A epoxy (DGEBA)", 28.0, 48.0),
        ("Polyamide hardener", 8.0, 22.0),
    ],
    ProductDomain.degreaser: [
        ("Nonionic surfactant (C12-14 EO7)", 2.0, 12.0),
        ("Sodium metasilicate", 2.0, 14.0),
    ],
    # surface_treatment: use substrate_default_levers() in levers.py (SSOT)
}


def effective_project_id(req: Requirement) -> str:
    if req.project_id:
        return req.project_id
    if req.product_type:
        return req.product_type.strip().lower().replace(" ", "_")[:64]
    return req.domain.value


def normalize_requirement(req: Requirement) -> Requirement:
    """Fill product_type/application from legacy fields when empty."""
    data = req.model_dump()
    if not data.get("product_type"):
        data["product_type"] = _DOMAIN_LABELS.get(req.domain, req.domain.value)
    if not data.get("application"):
        data["application"] = req.substrate.value
    if not data.get("project_id"):
        data["project_id"] = effective_project_id(req)
    return Requirement(**data)


def primary_objective(req: Requirement) -> str:
    objectives = req.objectives
    if objectives:
        return objectives[0].metric
    from ..pipeline.workflow import OBJECTIVE

    return OBJECTIVE.get(req.domain, "salt_spray_hours")


def default_objectives_for(req: Requirement) -> list[ObjectiveSpec]:
    from ..pipeline.workflow import default_objectives

    return default_objectives(req.domain)


def _bounds_from_pct(current: float, *, margin: float = 0.3) -> tuple[float, float]:
    lo = max(0.0, current * (1.0 - margin))
    hi = min(100.0, current * (1.0 + margin))
    if hi - lo < 1.0:
        hi = min(100.0, lo + 1.0)
    return round(lo, 4), round(hi, 4)


def derive_levers_from_formulation(form: Formulation) -> list[LeverSpec]:
    """Pick adjustable ingredients from a formulation (exclude fixed roles)."""
    levers: list[LeverSpec] = []
    for ing in form.ingredients:
        if ing.role in _FIXED_ROLES:
            continue
        if ing.weight_pct <= 0:
            continue
        lo, hi = _bounds_from_pct(ing.weight_pct)
        levers.append(LeverSpec(name=ing.name, low=lo, high=hi, unit="wt%"))
    return levers[:6]


def derive_process_levers(req: Requirement) -> list[LeverSpec]:
    levers: list[LeverSpec] = []
    cure = req.cure_temperature_c
    if cure is not None and req.domain == ProductDomain.anticorrosion_coating:
        levers.append(
            LeverSpec(
                name="cure_temperature_c",
                low=max(20.0, cure - 30),
                high=float(cure),
                unit="C",
            )
        )
    return levers


def default_levers_for(
    domain: ProductDomain,
    substrate: Substrate = Substrate.carbon_steel,
    *,
    cure_temperature_c: float | None = None,
) -> list[LeverSpec]:
    """UI / API default DOE levers — same resolution as ``resolve_levers`` without explicit levers."""
    req = Requirement(
        domain=domain,
        substrate=substrate,
        cure_temperature_c=cure_temperature_c,
        levers=[],
    )
    return resolve_levers(req)


def resolve_levers(req: Requirement, form: Formulation | None = None) -> list[LeverSpec]:
    """Resolve DOE levers: explicit > substrate defaults > formulation > legacy domain table."""
    if req.levers:
        return list(req.levers)
    substrate_levers = substrate_default_levers(req)
    if substrate_levers:
        return substrate_levers
    source = form or req.active_formulation
    if source and source.ingredients:
        derived = derive_levers_from_formulation(source)
        if derived:
            return derived + derive_process_levers(req)
    legacy = _LEGACY_LEVERS.get(req.domain, [])
    levers = [LeverSpec(name=n, low=lo, high=hi, unit="wt%") for n, lo, hi in legacy]
    return levers + derive_process_levers(req)


def levers_to_doe_factors(levers: list[LeverSpec]) -> list[DOEFactor]:
    return [DOEFactor(name=l.name, low=l.low, high=l.high, unit=l.unit) for l in levers]


def lever_snapshot_from_plan(plan, req: Requirement | None = None) -> list[dict]:
    """Persist DOE levers on Campaign — derive from plan.runs when factors list is empty."""
    from .schemas import ProductDomain

    if req and req.levers:
        return [lev.model_dump() for lev in req.levers]
    if plan.factors:
        return [{"name": f.name, "low": f.low, "high": f.high, "unit": f.unit} for f in plan.factors]

    domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
    if req:
        resolved = {lev.name: (lev.low, lev.high, lev.unit) for lev in resolve_levers(req)}
    else:
        resolved = {}
    legacy_map = {name: (lo, hi) for name, lo, hi in _LEGACY_LEVERS.get(domain, [])}
    keys: list[str] = []
    seen: set[str] = set()
    values: dict[str, list[float]] = {}
    for run in plan.runs:
        for key, raw in (run.natural or {}).items():
            if key not in seen:
                seen.add(key)
                keys.append(key)
            try:
                values.setdefault(key, []).append(float(raw))
            except (TypeError, ValueError):
                continue

    snapshot: list[dict] = []
    for name in keys:
        if name in resolved:
            lo, hi, unit = resolved[name]
        elif name in legacy_map:
            lo, hi = legacy_map[name]
            unit = "C" if name in ("cure_temperature_c", "bath_temperature_c") else "wt%"
        else:
            samples = values.get(name) or [50.0]
            lo, hi = _bounds_from_pct(sum(samples) / len(samples))
            unit = "C" if name in ("cure_temperature_c", "bath_temperature_c") else (
                "min" if name == "immersion_time_min" else "wt%"
            )
        snapshot.append({"name": name, "low": lo, "high": hi, "unit": unit})
    return snapshot


def formulation_from_materials(req: Requirement) -> Formulation | None:
    if not req.materials:
        return None
    ings = [
        Ingredient(
            name=m.name,
            role=m.role,
            weight_pct=m.weight_pct,
            smiles=m.smiles,
            formula=m.formula,
        )
        for m in req.materials
    ]
    name = req.product_type or req.domain.value
    return Formulation(name=name, domain=req.domain, ingredients=ings, rationale="project materials")
