"""Objective contract helpers — normalize, validate, and list metrics for closed-loop."""
from __future__ import annotations

import logging
from uuid import uuid4

from .schemas import ObjectiveSpec, ProductDomain, Requirement

logger = logging.getLogger(__name__)

# v22-fix: metric 别名表下移到 domain 层（避免 domain→service 倒置）。
# normalize_objective 在此规范化 metric，全链路受益。
METRIC_ALIASES: dict[str, str] = {
    "耐盐雾": "salt_spray_hours",
    "salt spray": "salt_spray_hours",
    "salt_spray": "salt_spray_hours",
    "清洗率": "cleaning_efficiency",
    "cost": "cost_cny_per_kg",
    "voc": "voc_gpl",
}


def _resolve_metric_name(metric: str) -> str:
    return METRIC_ALIASES.get(metric.strip().lower(), METRIC_ALIASES.get(metric, metric))


# v27 P1-8: 规范 metric 名集合 —— 显式 objectives 强校验用。
# 必须与 predictor 可产出的 props 键一致（predictor.py 的 props[...] 赋值）；
# 不在此集合的 metric 下游 props.get(metric, 0.0) 会静默 0 分错排。
# tests/test_objective_contract.py 有漂移测试守护两者一致。
KNOWN_METRICS: frozenset[str] = frozenset(
    {
        "salt_spray_hours",
        "cleaning_efficiency",
        "cost_cny_per_kg",
        "voc_gpl",
        "sustainability_idx",
        "coating_weight_gsm",
        "film_weight_gsm",
        "adhesion_mpa",
        "pencil_hardness_idx",
        "foam_index",
        "bath_life_cycles",
        "adhesion_promotion_idx",
    }
)


# v28 RC-1: 各 domain 的 predictor 实际产出指标（与 predictor.py 的 domain 分支对齐）。
# 通用指标（_cost_and_sustainability）所有 domain 都产出。
# 显式 objectives 的 metric 合法但不在当前 domain 集合时，下游
# props.get(metric, 0.0) 会静默 0 分错排 —— API 层 422 拦截。
# tests/test_objective_contract.py 有漂移测试守护本映射与 predictor 实际产出一致。
_UNIVERSAL_METRICS: frozenset[str] = frozenset(
    {"cost_cny_per_kg", "voc_gpl", "sustainability_idx"}
)
_DOMAIN_METRICS: dict[str, frozenset[str]] = {
    # key 取 ProductDomain.value，避免 domain→service 倒置（v22 原则）
    "anticorrosion_coating": frozenset(
        {"salt_spray_hours", "film_weight_gsm", "adhesion_mpa", "pencil_hardness_idx"}
    ),
    "degreaser": frozenset({"cleaning_efficiency", "foam_index", "bath_life_cycles"}),
    "surface_treatment": frozenset(
        {"coating_weight_gsm", "salt_spray_hours", "adhesion_promotion_idx"}
    ),
    # autodeposition_coating 走 predictor 的 else（surface_treatment）分支
    "autodeposition_coating": frozenset(
        {"coating_weight_gsm", "salt_spray_hours", "adhesion_promotion_idx"}
    ),
}


def domain_applicable_metrics(domain: ProductDomain) -> frozenset[str]:
    """当前 domain 的 predictor 实际产出的指标集合（含通用指标）。"""
    key = domain.value if isinstance(domain, ProductDomain) else str(domain)
    return _UNIVERSAL_METRICS | _DOMAIN_METRICS.get(key, frozenset())


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
    # v22-fix: 规范化 metric 别名（single choke point，全链路受益）。
    if data.get("metric"):
        data["metric"] = _resolve_metric_name(data["metric"])
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


def empty_measurements_template(objectives: list[ObjectiveSpec]) -> dict[str, None]:
    return {o.metric: None for o in objectives}


def validate_measurements(
    measurements: dict,
    objectives: list[ObjectiveSpec],
    *,
    strict: bool = False,
    report: list[str] | None = None,
) -> dict:
    """Return cleaned measurements; unknown keys dropped unless strict raises.

    When ``report`` is provided, each dropped key is appended (e.g.
    ``"unknown_key:viscosity"``) so callers can surface data-quality losses
    instead of silently discarding them.
    """
    allowed = set(objective_metrics(objectives))
    if not allowed:
        return dict(measurements or {})
    out: dict = {}
    for key, val in (measurements or {}).items():
        if key in allowed:
            out[key] = val
        elif strict:
            raise ValueError(f"Unknown measurement key {key!r}; allowed: {sorted(allowed)}")
        else:
            if report is not None:
                report.append(f"unknown_key:{key}")
            logger.warning(
                "validate_measurements dropped unknown key %r (allowed: %s)",
                key,
                sorted(allowed),
            )
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


def align_dataframe_measurement_columns(df, metrics: list[str], *, log=None):
    """Ensure DataFrame contains all objective metric columns (SSOT = metric name).

    Non-metric columns (DOE factors) are preserved. Missing metrics are filled
    with NaN. Column order: factors first, then metrics in contract order.
    """
    import logging

    log = log or logging.getLogger(__name__)
    if df is None or getattr(df, "empty", True) or not metrics:
        return df

    out = df.copy()
    factor_cols = [c for c in out.columns if c not in metrics]
    for m in metrics:
        if m not in out.columns:
            log.warning("Measurement column %r missing from DataFrame; filling NaN", m)
            out[m] = float("nan")
    ordered = factor_cols + [m for m in metrics if m in out.columns]
    return out[ordered]


def assert_dataframe_measurement_columns(df, metrics: list[str]) -> None:
    """Raise ValueError if any required metric column is entirely absent (all NaN ok)."""
    if df is None or getattr(df, "empty", True):
        return
    missing = [m for m in metrics if m not in df.columns]
    if missing:
        raise ValueError(f"DataFrame missing required measurement columns: {missing}")

