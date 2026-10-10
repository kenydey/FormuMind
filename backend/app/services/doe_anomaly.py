"""Lightweight anomaly detection for completed DOE / lab records (P1)."""
from __future__ import annotations

from ..domain.project_spec import primary_objective
from ..domain.schemas import AnomalyFlag, ExperimentRecord, ProductDomain, Requirement
from .doe_explain import experiment_id, k_nearest_experiments


_RESIDUAL_Z_THRESHOLD = 2.5
# Range-normalised RMS distance (max 1.0): far from every other experiment.
_FACTOR_OUTLIER_DISTANCE = 0.6


def _physical_limit_checks(
    eid: str,
    req: Requirement,
    metric: str,
    actual: float,
) -> list[AnomalyFlag]:
    flags: list[AnomalyFlag] = []

    if metric == "salt_spray_hours" and req.domain == ProductDomain.anticorrosion_coating:
        target = float(req.salt_spray_hours) if req.salt_spray_hours is not None else 500.0
        upper = max(2500.0, target * 4.0)
        if actual > upper:
            flags.append(
                AnomalyFlag(
                    experiment_id=eid,
                    type="physical_limit",
                    severity="warning",
                    note=f"盐雾 {actual:.0f}h 超出合理上限 {upper:.0f}h，建议复测验证。",
                    actual=actual,
                )
            )
        if actual < 0:
            flags.append(
                AnomalyFlag(
                    experiment_id=eid,
                    type="physical_limit",
                    severity="critical",
                    note="盐雾时长为负值，数据无效。",
                    actual=actual,
                )
            )

    if metric == "cleaning_efficiency":
        if actual > 100.0 or actual < 0:
            flags.append(
                AnomalyFlag(
                    experiment_id=eid,
                    type="physical_limit",
                    severity="warning",
                    note=f"清洗率 {actual:.1f}% 超出 [0, 100] 物理范围。",
                    actual=actual,
                )
            )

    voc_limit = req.voc_limit_gpl
    if voc_limit is not None and metric == "voc_gpl" and actual > voc_limit * 1.25:
        flags.append(
            AnomalyFlag(
                experiment_id=eid,
                type="physical_limit",
                severity="info",
                note=f"VOC {actual:.0f} g/L 显著高于限值 {voc_limit:.0f} g/L。",
                actual=actual,
            )
        )

    return flags


def detect_anomalies(
    req: Requirement,
    existing: list[ExperimentRecord],
) -> list[AnomalyFlag]:
    """Flag suspicious completed experiments using residual + rule checks."""
    if not existing:
        return []

    from .active_learning import _surrogate_score

    metric = primary_objective(req)
    flags: list[AnomalyFlag] = []

    for idx, exp in enumerate(existing):
        eid = experiment_id(exp, idx)
        actual = (exp.measured or {}).get(metric)
        if actual is None:
            continue

        mean, std = _surrogate_score(exp.factors or {}, req.domain, existing[:idx] + existing[idx + 1 :], metric)
        # P1-4: 小样本保护 —— n<10 时 std 估计不可靠，z 阈值放宽到 3.5，
        # 且 std 加相对地板（避免干净数据下 z 虚高导致误杀风暴）
        _n = len(existing)
        _z_thresh = 3.5 if _n < 10 else _RESIDUAL_Z_THRESHOLD
        _std_floor = 0.05 * (abs(mean) + 1e-6)
        _std_eff = max(std, _std_floor)
        if _std_eff > 1e-6:
            z = abs(actual - mean) / _std_eff
            if z > _z_thresh:
                severity = "critical" if z > 4.0 else "warning"
                flags.append(
                    AnomalyFlag(
                        experiment_id=eid,
                        type="high_residual",
                        severity=severity,
                        note=f"实测与代理模型预测偏差较大（z={z:.1f}），建议复测。",
                        predicted=round(mean, 3),
                        actual=round(float(actual), 3),
                    )
                )

        flags.extend(_physical_limit_checks(eid, req, metric, float(actual)))

        if len(existing) >= 5:
            others = [e for i, e in enumerate(existing) if i != idx]
            nearest = k_nearest_experiments(exp.factors or {}, others, k=1)
            if nearest and nearest[0][0] is not None:
                from .doe_explain import _factor_distance, factor_spans

                dist = _factor_distance(
                    exp.factors or {},
                    nearest[0][0].factors or {},
                    factor_spans([e.factors or {} for e in existing]),
                )
                if dist > _FACTOR_OUTLIER_DISTANCE:
                    flags.append(
                        AnomalyFlag(
                            experiment_id=eid,
                            type="outlier_in_factor_space",
                            severity="info",
                            note="该点在因子空间中远离已有实验簇，可能是探索性边界点或录入错误。",
                        )
                    )

    return flags
