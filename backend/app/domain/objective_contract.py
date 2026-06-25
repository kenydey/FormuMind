"""Objective contract helpers — normalize, validate, and list metrics for closed-loop."""
from __future__ import annotations

from uuid import uuid4

from .schemas import ObjectiveSpec, ProductDomain, Requirement

_METRIC_UNITS: dict[str, str] = {
    "salt_spray_hours": "h",
    "cleaning_efficiency": "%",
    "cost_cny_per_kg": "CNY/kg",
    "voc_gpl": "g/L",
    "sustainability_idx": "",
    "coating_weight_gsm": "g/m²",
    "film_weight_gsm": "g/m²",
    "ph_value": "",
}

_METRIC_LABELS: dict[str, str] = {
    "salt_spray_hours": "耐盐雾 Salt Spray",
    "cleaning_efficiency": "清洗率 Cleaning",
    "cost_cny_per_kg": "成本 Cost",
    "voc_gpl": "VOC",
    "sustainability_idx": "可持续性",
    "coating_weight_gsm": "膜重",
    "film_weight_gsm": "干膜重",
    "ph_value": "pH",
}


def default_unit(metric: str) -> str:
    return _METRIC_UNITS.get(metric, "")


def default_display_name(metric: str) -> str:
    return _METRIC_LABELS.get(metric, metric.replace("_", " "))


def normalize_objective(obj: ObjectiveSpec) -> ObjectiveSpec:
    data = obj.model_dump()
    if not data.get("id"):
        data["id"] = data.get("metric") or uuid4().hex[:8]
    if not data.get("display_name"):
        data["display_name"] = default_display_name(data["metric"])
    if not data.get("unit"):
        data["unit"] = default_unit(data["metric"])
    direction = data.get("direction") or "maximize"
    if direction not in ("maximize", "minimize", "match_target"):
        direction = "maximize"
    data["direction"] = direction
    return ObjectiveSpec(**data)


def normalize_objectives(req: Requirement) -> list[ObjectiveSpec]:
    from ..pipeline.workflow import default_objectives

    raw = req.objectives or default_objectives(req.domain)
    return [normalize_objective(o) for o in raw]


def objective_metrics(objectives: list[ObjectiveSpec]) -> list[str]:
    return [o.metric for o in objectives if o.metric]


def primary_objective_metric(objectives: list[ObjectiveSpec], domain: ProductDomain) -> str:
    if objectives:
        return objectives[0].metric
    from ..pipeline.workflow import OBJECTIVE

    return OBJECTIVE[domain]


def empty_measurements_template(objectives: list[ObjectiveSpec]) -> dict[str, None]:
    return {o.metric: None for o in objectives}


def validate_measurements(
    measurements: dict,
    objectives: list[ObjectiveSpec],
    *,
    strict: bool = False,
) -> dict:
    """Return cleaned measurements; unknown keys dropped unless strict raises."""
    allowed = set(objective_metrics(objectives))
    if not allowed:
        return dict(measurements or {})
    out: dict = {}
    for key, val in (measurements or {}).items():
        if key in allowed:
            out[key] = val
        elif strict:
            raise ValueError(f"Unknown measurement key {key!r}; allowed: {sorted(allowed)}")
    return out


def row_has_required_measurements(
    measurements: dict,
    objectives: list[ObjectiveSpec],
    *,
    require_all: bool = False,
) -> bool:
    """True when enough objective metrics are filled for Completed status."""
    cleaned = validate_measurements(measurements, objectives)
    filled = [
        k
        for k, v in cleaned.items()
        if v is not None and v != "" and not (isinstance(v, float) and v != v)
    ]
    if not objectives:
        return bool(filled)
    if require_all:
        return len(filled) >= len(objectives)
    # Default: primary (first) objective must be filled
    primary = objectives[0].metric
    return primary in filled


def objectives_from_snapshot(snapshot: list | None, domain: ProductDomain) -> list[ObjectiveSpec]:
    if not snapshot:
        from ..pipeline.workflow import default_objectives

        return [normalize_objective(o) for o in default_objectives(domain)]
    return [normalize_objective(ObjectiveSpec(**item)) for item in snapshot]
