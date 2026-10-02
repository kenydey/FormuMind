"""v7 H-1: 离散因子（str 值）不應使 ExperimentRecord / run_optimization 崩溃。"""
from __future__ import annotations


def test_experiment_record_accepts_str_factors():
    from app.domain.schemas import ExperimentRecord, ProductDomain

    rec = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={"树脂": "聚氨酯树脂X", "固化温度": 120.0},
        measured={"附着力": 5.0},
    )
    assert rec.factors["树脂"] == "聚氨酯树脂X"
    assert rec.factors["固化温度"] == 120.0


def test_factor_spans_skips_str_values():
    from app.domain.schemas import ExperimentRecord, ProductDomain
    from app.services.failure_memory import factor_spans

    recs = [
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={"树脂": "A", "温度": 100.0},
            measured={"y": 1.0},
        ),
        ExperimentRecord(
            domain=ProductDomain.anticorrosion_coating,
            factors={"树脂": "B", "温度": 120.0},
            measured={"y": 2.0},
        ),
    ]
    spans = factor_spans(recs, {"温度": 110.0})
    # 温度有数值跨度；树脂是 str，不应出现在 spans 里也不应崩
    assert "温度" in spans
    assert "树脂" not in spans
