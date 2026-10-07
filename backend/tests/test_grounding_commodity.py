"""Strict grounding must not strip a recipe of its commodity ingredients.

Before: a realistic epoxy zinc primer (resin, hardener, zinc phosphate, TiO2,
xylene, butyl acetate, defoamer, talc) lost xylene / butyl acetate / defoamer /
talc — none of them is in the small material catalog or the retrieved evidence —
and came out as a 72 % recipe with no solvent, filler or additive. Because the
weights are (deliberately) not renormalised, its predicted VOC was 0 g/L, which
the "minimize VOC" objective then *rewarded*.

Commodity classes (solvent / filler / pigment, plus generically named additives)
are now kept — tagged "medium" and flagged for a human — while specialty
components without support (the actual hallucination risk) are still dropped.
"""
from __future__ import annotations

import pytest

from app.domain.schemas import (
    Evidence,
    ObjectiveSpec,
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaComponent as C,
    RecommendedFormulaListResponse,
    Requirement,
)
from app.services.grounded_recommend import ground_recommended_formulas


def _formula(*comps: C) -> RecommendedFormula:
    return RecommendedFormula(
        name="Epoxy zinc primer", domain=ProductDomain.anticorrosion_coating, components=list(comps)
    )


PRIMER = [
    C(name="Bisphenol-A epoxy (DGEBA)", component_type="resin", weight_pct=35.0),
    C(name="Polyamide hardener", component_type="hardener", weight_pct=15.0),
    C(name="Zinc phosphate", component_type="inhibitor", weight_pct=12.0, cas_no="7779-90-0"),
    C(name="Titanium dioxide", component_type="pigment", weight_pct=10.0),
    C(name="Xylene", component_type="solvent", weight_pct=12.0),
    C(name="Butyl acetate", component_type="solvent", weight_pct=8.0),
    C(name="Polyether-modified siloxane defoamer", component_type="additive", weight_pct=0.5),
    C(name="Talc", component_type="filler", weight_pct=7.5),
]


def test_commodity_components_are_kept_and_flagged_not_dropped():
    out, warnings = ground_recommended_formulas([_formula(*PRIMER)], [])

    comps = {c.name: c for c in out[0].components}
    assert set(comps) == {c.name for c in PRIMER}, "nothing may be dropped"
    for name in ("Xylene", "Butyl acetate", "Talc", "Polyether-modified siloxane defoamer"):
        assert comps[name].grounding_confidence == "medium", name
    assert sum(c.weight_pct or 0 for c in out[0].components) == pytest.approx(100.0)
    assert any("常规成分未在证据/材料库中核实" in w for w in out[0].warnings)
    assert not any("剔除" in w for w in warnings)


def test_specialty_components_without_support_are_still_dropped():
    formula = _formula(
        C(name="Zinc phosphate", component_type="inhibitor", weight_pct=10.0, cas_no="7779-90-0"),
        C(name="Imaginaryium-X999", component_type="inhibitor", weight_pct=5.0),
        C(name="Obscure additive QZX-42", component_type="additive", weight_pct=1.0),
        C(name="Mystery crosslinker QQQ-7", component_type="hardener", weight_pct=3.0),
    )
    out, warnings = ground_recommended_formulas([formula], [])

    assert [c.name for c in out[0].components] == ["Zinc phosphate"]
    assert any("Imaginaryium-X999" in w and "剔除" in w for w in warnings)


@pytest.mark.parametrize("role", ["溶剂", "solvent", "Solvent", "填料", "颜料"])
def test_commodity_roles_are_recognised_in_chinese_and_english(role):
    out, _ = ground_recommended_formulas(
        [_formula(C(name="Some commodity thing", component_type=role, weight_pct=10.0))], []
    )
    assert out and out[0].components[0].grounding_confidence == "medium"


def test_a_role_less_unknown_component_is_still_dropped():
    """No component_type → no exemption (an LLM that omits the role gets no free pass)."""
    out, _ = ground_recommended_formulas(
        [_formula(C(name="Xylene-like unknown stuff", weight_pct=10.0))], []
    )
    assert out == []


def test_evidence_backed_commodity_stays_high():
    ev = [Evidence(source="patent", identifier="US1", title="Xylene solvent", snippet="Xylene is used as the solvent in the primer", relevance=0.9)]
    out, _ = ground_recommended_formulas(
        [_formula(C(name="Xylene", component_type="solvent", weight_pct=10.0))], ev
    )
    assert out[0].components[0].grounding_confidence in {"high", "medium"}
    assert out[0].components[0].name == "Xylene"


def test_non_strict_mode_also_tags_commodity_as_medium():
    out, _ = ground_recommended_formulas(
        [_formula(C(name="Xylene", component_type="solvent", weight_pct=10.0))], [], strict=False
    )
    assert out[0].components[0].grounding_confidence == "medium"


def test_pipeline_keeps_the_solvent_so_voc_is_not_scored_as_zero():
    """End to end through run_recommend_orchestration."""
    from app.services.recommend_pipeline import run_recommend_orchestration

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="voc_gpl", weight=0.4, direction="minimize"),
        ],
    )
    bundle = run_recommend_orchestration(
        req, [], requested_n=1, objectives=req.objectives, include_tradeoff=False,
        synth_override=RecommendedFormulaListResponse(formulas=[_formula(*PRIMER)], engine="llm"),
    )

    (form,) = bundle.scored
    names = {i.name for i in form.ingredients}
    assert {"Xylene", "Butyl acetate", "Talc"} <= names
    assert sum(i.weight_pct for i in form.ingredients) == pytest.approx(100.0, abs=0.01)
    assert form.predicted["voc_gpl"] > 0.0, "solvents were kept, VOC must reflect them"
    assert not any("sum to" in w and "expected ~100" in w for w in form.warnings)


# ── verbatim mentions ground a component, whatever its length or script ─────


def _one(component: C, snippet: str):
    anchor = C(name="Zinc phosphate", component_type="inhibitor", weight_pct=10.0, cas_no="7779-90-0")
    ev = [Evidence(source="patent", identifier="US1", title="Doc", snippet=snippet, relevance=0.9)]
    out, _ = ground_recommended_formulas([_formula(anchor, component)], ev)
    return {c.name: c for c in out[0].components}.get(component.name)


@pytest.mark.parametrize(
    "name,role,snippet",
    [
        ("Benzotriazole", "inhibitor", "Benzotriazole is a corrosion inhibitor for copper alloys."),
        ("Benzotriazole", "inhibitor", "Benzotriazole (BTA) 0.5 wt% improves salt spray resistance."),
        ("Silane", "active", "An epoxy silane coupling agent improves adhesion."),
        ("Silanes", "active", "A silane coupling agent improves adhesion."),   # plural name, singular evidence
        ("Benzotriazole", "inhibitor", "Two benzotriazoles were compared."),   # plural evidence
        ("苯并三氮唑", "inhibitor", "苯并三氮唑作为缓蚀剂添加量 0.5%。"),      # CJK is not tokenised
        ("Bisphenol-A epoxy resin (DGEBA)", "resin", "DGEBA resins cured with polyamide."),  # parenthetical
    ],
)
def test_a_component_the_evidence_names_verbatim_is_grounded(name, role, snippet):
    comp = _one(C(name=name, component_type=role, weight_pct=1.0), snippet)
    assert comp is not None, "strict grounding must not drop a component the evidence names"
    assert comp.grounding_confidence == "high"
    assert comp.evidence_refs == ["US1"]


@pytest.mark.parametrize(
    "name,snippet",
    [
        ("Acid", "An acidic bath at pH 3 is used."),             # word boundary: acid ≠ acidic
        ("Mystery-QQ", "Nothing relevant here."),
        ("Zn", "Zn plating is common."),                         # too short to be specific
    ],
)
def test_substring_coincidences_do_not_ground(name, snippet):
    assert _one(C(name=name, component_type="inhibitor", weight_pct=1.0), snippet) is None


def test_existing_refs_are_kept_when_the_evidence_also_names_the_component():
    comp = _one(
        C(name="Benzotriazole", component_type="inhibitor", weight_pct=1.0, evidence_refs=["US1"]),
        "Benzotriazole inhibits copper corrosion.",
    )
    assert comp.evidence_refs == ["US1"] and comp.grounding_confidence == "high"


def test_v16_prefilled_hallucinated_ref_rejected_in_pipeline():
    """v16: 预填的幻觉 ID 被剔除，回退到真实证据匹配（v15-4 新语义）。"""
    comp = _one(
        C(name="Benzotriazole", component_type="inhibitor", weight_pct=1.0, evidence_refs=["LLM-picked"]),
        "Benzotriazole inhibits copper corrosion.",
    )
    assert "LLM-picked" not in comp.evidence_refs
    assert comp.evidence_refs == ["US1"]


def test_v16_cas_branch_rejects_unrelated_prefilled_ref():
    """v16 P1-4: CAS 命中但预填了无关真实 ID → 剔除重算，不标 high 张冠李戴。"""
    anchor = C(name="Zinc phosphate", component_type="inhibitor", weight_pct=10.0, cas_no="7779-90-0")
    comp = C(
        name="Zinc phosphate",
        component_type="inhibitor",
        weight_pct=5.0,
        cas_no="7779-90-0",
        evidence_refs=["EP999"],  # 真实存在但与磷酸锌无关
    )
    ev = [
        Evidence(source="patent", identifier="EP999", title="Epoxy resin doc",
                 snippet="Epoxy resin cures fast.", relevance=0.9),
        Evidence(source="patent", identifier="US2", title="Zinc phosphate doc",
                 snippet="Zinc phosphate 7779-90-0 inhibits corrosion.", relevance=0.9),
    ]
    out, _ = ground_recommended_formulas([_formula(anchor, comp)], ev)
    got = {c.name: c for c in out[0].components}["Zinc phosphate"]
    assert "EP999" not in got.evidence_refs, "无关预填 ID 不得保留"
    # 相关性复核后应回退到真实匹配（US2 提到磷酸锌）
    assert "US2" in got.evidence_refs


def test_v16_id_map_case_collision_keeps_both():
    """v16 P2-5: identifier 大小写碰撞不再互相覆盖。"""
    from app.services.grounded_recommend import _evidence_corpus

    ev = [
        Evidence(source="patent", identifier="US1", title="Doc A",
                 snippet="Zinc phosphate test.", relevance=0.9),
        Evidence(source="patent", identifier="us1", title="Doc B",
                 snippet="Epoxy resin test.", relevance=0.9),
    ]
    _, _, id_map, _ = _evidence_corpus(ev)
    assert sorted(id_map["us1"]) == ["US1", "us1"]

