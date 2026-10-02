"""U-5: 离散材料替换语义。

离散因子的字符串水平 + lever.material_map → 成分身份替换（wt% 不变），
而非 B-DOE-3 的静默跳过。无映射时保持旧的跳过行为。
"""

from __future__ import annotations

from app.domain.project_spec import normalize_requirement
from app.domain.schemas import (
    Formulation,
    Ingredient,
    LeverSpec,
    ProductDomain,
    Requirement,
)
from app.pipeline import reconstruct


def _req_with_levers(levers: list[LeverSpec]) -> Requirement:
    active = Formulation(
        name="测试配方",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name="树脂A", weight_pct=40.0, role="成膜物"),
            Ingredient(name="固化剂B", weight_pct=10.0, role="固化剂"),
            Ingredient(name="溶剂C", weight_pct=50.0, role="溶剂"),
        ],
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        active_formulation=active,
        levers=levers,
    )
    return normalize_requirement(req)


def _material_lever(**kw) -> LeverSpec:
    base = dict(
        name="树脂A",
        low=0.0,
        high=0.0,
        unit="wt%",
        kind="discrete",
        levels=["树脂A", "树脂B"],
    )
    base.update(kw)
    return LeverSpec(**base)


def test_string_level_with_material_map_replaces_identity() -> None:
    req = _req_with_levers([_material_lever(material_map={"树脂B": "聚氨酯树脂X"})])
    out = reconstruct.formulation_from_factors(req, {"树脂A": "树脂B"})
    names = [i.name for i in out.ingredients]
    assert "聚氨酯树脂X" in names
    assert "树脂A" not in names
    # wt% 不变：被替换成分继承原配比骨架
    replaced = next(i for i in out.ingredients if i.name == "聚氨酯树脂X")
    assert replaced.weight_pct == 40.0
    total = sum(i.weight_pct for i in out.ingredients)
    assert abs(total - 100.0) < 1.0


def test_string_level_without_material_map_stays_skipped() -> None:
    """无映射 → 保持 B-DOE-3 的 fail-safe 跳过（不做猜测）。"""
    req = _req_with_levers([_material_lever()])
    out = reconstruct.formulation_from_factors(req, {"树脂A": "树脂B"})
    names = [i.name for i in out.ingredients]
    assert "树脂A" in names  # 未被替换


def test_unmapped_level_with_map_stays_skipped() -> None:
    """映射里没有该水平 → 同样跳过，不抛错。"""
    req = _req_with_levers([_material_lever(material_map={"树脂B": "聚氨酯树脂X"})])
    out = reconstruct.formulation_from_factors(req, {"树脂A": "树脂C"})
    names = [i.name for i in out.ingredients]
    assert "树脂A" in names


def test_name_collision_skips_replacement() -> None:
    """目标名已存在 → fail-open 跳过并保持原成分。"""
    req = _req_with_levers([_material_lever(material_map={"树脂B": "固化剂B"})])
    out = reconstruct.formulation_from_factors(req, {"树脂A": "树脂B"})
    names = [i.name for i in out.ingredients]
    assert "树脂A" in names  # 撞名，跳过替换
    assert names.count("固化剂B") == 1  # 无重复


def test_numeric_discrete_still_overrides_weight() -> None:
    """数值离散因子走原 wt% 覆盖路径，不受 U-5 影响。"""
    lever = LeverSpec(
        name="固化剂B", low=5.0, high=15.0, unit="wt%",
        kind="discrete", levels=[5.0, 10.0, 15.0],
    )
    req = _req_with_levers([lever])
    out = reconstruct.formulation_from_factors(req, {"固化剂B": 15.0})
    ing = next(i for i in out.ingredients if i.name == "固化剂B")
    assert ing.weight_pct == 15.0
