"""Up-4A: 离散字符串因子生产链回归（B-DOE-3 float() 崩溃修复）。"""
from __future__ import annotations

import pandas as pd

from app.domain.schemas import DOEFactor, LeverSpec
from app.domain.project_spec import levers_to_doe_factors
from app.services.engines.adapters.doe_adapter import dataframe_to_doe_plan


def _discrete_factor() -> DOEFactor:
    return DOEFactor(
        name="固化剂种类",
        low=0.0,
        high=1.0,
        unit="",
        kind="discrete",
        levels=["聚酰胺", "酚醛", "异氰酸酯"],
    )


def test_dataframe_to_doe_plan_string_levels_no_crash():
    # BayBE CategoricalParameter 返回字符串水平，旧代码 float() 必崩。
    df = pd.DataFrame(
        {
            "固化剂种类": ["聚酰胺", "酚醛", "异氰酸酯"],
            "树脂wt": [50.0, 55.0, 60.0],
        }
    )
    factors = [
        _discrete_factor(),
        DOEFactor(name="树脂wt", low=40.0, high=70.0, unit="wt%"),
    ]
    plan = dataframe_to_doe_plan(df, factors, "full_factorial", engine="baybe")
    assert len(plan.runs) == 3
    assert plan.runs[0].natural["固化剂种类"] == "聚酰胺"
    assert plan.runs[1].natural["固化剂种类"] == "酚醛"
    assert plan.runs[2].natural["固化剂种类"] == "异氰酸酯"
    # coded 按水平索引归一化：首水平 -1，末水平 +1
    assert plan.runs[0].coded["固化剂种类"] == -1.0
    assert plan.runs[2].coded["固化剂种类"] == 1.0
    # 连续因子不受影响
    assert plan.runs[0].natural["树脂wt"] == 50.0


def test_dataframe_to_doe_plan_numeric_discrete_levels():
    df = pd.DataFrame({"层数": [1.0, 2.0, 3.0]})
    factors = [
        DOEFactor(name="层数", low=1.0, high=3.0, unit="", kind="discrete", levels=[1.0, 2.0, 3.0])
    ]
    plan = dataframe_to_doe_plan(df, factors, "full_factorial", engine="baybe")
    assert plan.runs[0].natural["层数"] == 1.0
    assert plan.runs[0].coded["层数"] == -1.0
    assert plan.runs[2].coded["层数"] == 1.0


def test_lever_spec_discrete_roundtrip():
    lev = LeverSpec(name="固化剂种类", low=0.0, high=1.0, unit="", kind="discrete",
                    levels=["聚酰胺", "酚醛"])
    factors = levers_to_doe_factors([lev])
    assert factors[0].kind == "discrete"
    assert factors[0].levels == ["聚酰胺", "酚醛"]


def test_formulation_from_factors_skips_string_level():
    # 字符串水平不能转成重量 —— 应跳过覆盖，不抛 ValueError。
    from app.domain.schemas import ProductDomain, Requirement
    from app.pipeline.reconstruct import formulation_from_factors

    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    form = formulation_from_factors(req, {"固化剂种类": "聚酰胺", "不存在组分": "x"})
    assert form is not None
    assert len(form.ingredients) > 0
