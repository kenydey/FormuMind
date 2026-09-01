"""P1 tests: MolScribe output validation + LLM smiles structure gate."""

import pytest

from app.domain.formulation_gate import (
    validate_formulations,
    validate_recommended_formulas,
)
from app.services.ocsr import validate_recognized_smiles

from app.domain.schemas import (
    Formulation,
    Ingredient,
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaComponent,
)

pytestmark = pytest.mark.skipif(
    not __import__("app.services.moljson", fromlist=["rdkit_available"]).rdkit_available(),
    reason="RDKit not importable in this environment",
)


def _formulation(ingredients: list[dict]) -> Formulation:
    ings = []
    for i in ingredients:
        kw = dict(i)
        kw.setdefault("role", "additive")
        kw.setdefault("weight_pct", 50.0)
        ings.append(Ingredient(name=kw.pop("name"), **kw))
    return Formulation(
        name="test-form",
        domain=ProductDomain.autodeposition_coating,
        ingredients=ings,
        rationale="test",
        predicted={},
        score=0.5,
        warnings=[],
    )


class TestValidateRecognizedSmiles:
    def test_valid_smiles_passthrough(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ocsr.predict_smiles_molscribe",
            lambda path: "CCO",
        )
        res = validate_recognized_smiles("/tmp/x.png")
        assert res["ok"] is True
        assert res["valid"] is True
        assert res["smiles"] == "CCO"
        assert res["atom_count"] == 3
        assert res["roundtrip_ok"] is True

    def test_invalid_smiles_marked_low_confidence(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ocsr.predict_smiles_molscribe",
            lambda path: "not-a-molecule",
        )
        res = validate_recognized_smiles("/tmp/x.png")
        assert res["ok"] is True  # recognized but...
        assert res["valid"] is False  # ...structurally unusable
        assert "reason" in res

    def test_no_smiles_reported_failure(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ocsr.predict_smiles_molscribe",
            lambda path: None,
        )
        res = validate_recognized_smiles("/tmp/x.png")
        assert res["ok"] is False
        assert res["valid"] is False


class TestFormulationGateSmiles:
    def test_valid_smiles_kept(self):
        forms, warnings = validate_formulations(
            [_formulation([{"name": "Ethanol", "smiles": "CCO"}])]
        )
        assert forms[0].ingredients[0].smiles == "CCO"
        assert not [w for w in warnings if "SMILES" in w]

    def test_invalid_smiles_cleared_with_warning(self):
        forms, warnings = validate_formulations(
            [_formulation([{"name": "Mystery", "smiles": "C1=CC=CC"}])]
        )
        # Invalid SMILES must be cleared so catalog re-fills it.
        assert forms[0].ingredients[0].smiles is None
        assert any("SMILES" in w and "无法被 RDKit 解析" in w for w in warnings)

    def test_llm_hallucinated_formula_cleared(self):
        # LLM 常编造如 "C(CO)3CH2" 之类畸形串
        forms, warnings = validate_formulations(
            [_formulation([{"name": "Resin", "smiles": "C(CO)3CH2"}])]
        )
        assert forms[0].ingredients[0].smiles is None
        assert any("结构幻觉" in w or "无法被 RDKit 解析" in w for w in warnings)


class TestRecommendedGateSmiles:
    def _rec(self, comps: list[dict]) -> RecommendedFormula:
        return RecommendedFormula(
            name="rec",
            domain=ProductDomain.autodeposition_coating,
            rationale="r",
            objectives_summary="o",
            components=[
                RecommendedFormulaComponent(
                    name=c["name"], smiles=c.get("smiles"), weight_pct=50.0
                )
                for c in comps
            ],
            predicted={},
            score=0.5,
            warnings=[],
            engine="llm",
        )

    def test_invalid_smiles_cleared_in_recommended(self):
        recs, warnings = validate_recommended_formulas(
            [self._rec([{"name": "A", "smiles": "BrBrBrO3X"}])]
        )
        assert recs[0].components[0].smiles is None
        assert any("SMILES" in w for w in warnings)
