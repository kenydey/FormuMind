"""U-2: 测量来源血缘打通 —— 寻优路径的 measurement_source 不再写死。

baybe_engine.run_optimization 与 workflow.run_optimization（numpy 路径）
此前硬编码 "predictor_virtual"，即使 measurements 里有真实 lab 数据。
现在两者都走 lab_measurement_source()。
"""

from __future__ import annotations

from app.domain.project_spec import normalize_requirement
from app.domain.schemas import ExperimentRecord, ProductDomain, Requirement
from app.pipeline.workflow import run_optimization


def _req() -> Requirement:
    return normalize_requirement(
        Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    )


def _lab_record() -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={"zinc_phosphate": 15.0},
        measured={"salt_spray_hours": 620.0},
        source="lab",
    )


def _complete_lab_record() -> ExperimentRecord:
    """A lab row BayBE can actually use.

    BayBE fails closed on a measurement that lacks any factor column of its search
    space (``_clean_measurement_dataframe``), and drops rows with a missing
    objective, so the row must carry *every* lever and *every* objective metric.
    Derived from the requirement itself so it cannot drift from the real lever
    names (the old ``{"zinc_phosphate": 15.0}`` fixture matched nothing).
    """
    from app.domain.objective_contract import normalize_objectives
    from app.pipeline.workflow import build_doe_factors

    req = _req()
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={f.name: (f.low + f.high) / 2 for f in build_doe_factors(req)},
        # req.objectives is empty here; the engine resolves the domain defaults.
        measured={
            o.metric: (620.0 if o.metric == "salt_spray_hours" else 50.0)
            for o in normalize_objectives(req)
        },
        source="lab",
    )


def test_numpy_path_reports_lab_when_seeded_with_lab() -> None:
    res = run_optimization(
        _req(), iterations=2, engine="numpy", existing_records=[_lab_record()]
    )
    assert res.measurement_source == "lab"


def test_numpy_path_reports_predictor_virtual_without_lab() -> None:
    res = run_optimization(_req(), iterations=2, engine="numpy", existing_records=[])
    assert res.measurement_source == "predictor_virtual"


def test_numpy_path_predictor_record_stays_virtual() -> None:
    rec = _lab_record().model_copy(update={"source": "predictor_virtual"})
    res = run_optimization(_req(), iterations=2, engine="numpy", existing_records=[rec])
    assert res.measurement_source == "predictor_virtual"


def test_baybe_path_uses_lab_measurement_source() -> None:
    """BayBE 路径同样走 lab_measurement_source（baybe 缺席时跳过）。"""
    pytest = __import__("pytest")
    from app.services.engines.doe_registry import baybe_available

    if not baybe_available():
        pytest.skip("baybe not installed")
    from app.services.engines.baybe_engine import BaybeCampaignEngine

    eng = BaybeCampaignEngine()
    if not eng.available():
        pytest.skip("baybe engine unavailable")
    res = eng.run_optimization(_req(), iterations=2, measurements=[_complete_lab_record()])
    assert res.measurement_source == "lab"
    res2 = eng.run_optimization(_req(), iterations=2, measurements=[])
    assert res2.measurement_source == "predictor_virtual"


def test_workbench_source_counts_as_real() -> None:
    """v13-3: source="workbench" 的真实测量默认进 GP，不再静默丢弃。"""
    from app.domain.schemas import ExperimentRecord, REAL_SOURCES
    from app.services.engines.adapters.measurements_adapter import records_to_dataframe
    from app.services.doe_cycle_service import lab_measurement_source

    assert "workbench" in REAL_SOURCES
    assert "lab" in REAL_SOURCES

    from app.domain.schemas import ProductDomain

    rec = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={"resin": 50.0},
        measured={"salt_spray_hours": 500.0},
        source="workbench",
    )
    req = _req()
    df = records_to_dataframe([rec], req, req.objectives)
    assert not df.empty, "workbench 真实测量默认应进 GP 训练集"
    assert lab_measurement_source([rec]) == "lab", "纯 workbench 不再误报 predictor_virtual"


def test_virtual_count_excludes_workbench():
    """v14-3: workbench 计入 lab 点数，不再被双计入 virtual。"""
    from app.domain.schemas import REAL_SOURCES, VIRTUAL_SOURCES

    # 口径自洽：REAL 与 VIRTUAL 不交叠
    assert not (REAL_SOURCES & VIRTUAL_SOURCES)
    assert "workbench" in REAL_SOURCES
    assert "workbench" not in VIRTUAL_SOURCES
    assert "baybe_opt" in VIRTUAL_SOURCES
    assert "predictor_virtual" in VIRTUAL_SOURCES
