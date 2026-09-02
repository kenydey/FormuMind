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
            "app.services.ocsr.predict_molscribe_with_confidence",
            lambda path: {"smiles": "CCO", "confidence": 0.95},
        )
        res = validate_recognized_smiles("/tmp/x.png")
        assert res["ok"] is True
        assert res["valid"] is True
        assert res["smiles"] == "CCO"
        assert res["atom_count"] == 3
        assert res["roundtrip_ok"] is True
        assert res["confidence"] == 0.95  # P-C: confidence passthrough

    def test_invalid_smiles_marked_low_confidence(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ocsr.predict_molscribe_with_confidence",
            lambda path: {"smiles": "not-a-molecule", "confidence": 0.4},
        )
        res = validate_recognized_smiles("/tmp/x.png")
        assert res["ok"] is True  # recognized but...
        assert res["valid"] is False  # ...structurally unusable
        assert "reason" in res

    def test_no_smiles_reported_failure(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ocsr.predict_molscribe_with_confidence",
            lambda path: {"smiles": None, "confidence": None},
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


class TestMolscribeFullAudit:
    """M-D: atom-level confidence audit from return_atoms_bonds."""

    def test_low_confidence_atoms_detected(self, monkeypatch):
        from app.services.ocsr import predict_molscribe_full

        monkeypatch.setattr("app.services.ocsr.molscribe_available", lambda: True)
        monkeypatch.setattr(
            "app.services.ocsr._get_molscribe_model",
            lambda: _FakeMolscribeModel(
                {
                    "smiles": "CCO",
                    "confidence": 0.7,
                    "atoms": [
                        {"atom_symbol": "C", "x": 1, "y": 1, "confidence": 0.9},
                        {"atom_symbol": "C", "x": 2, "y": 1, "confidence": 0.3},  # low
                        {"atom_symbol": "O", "x": 3, "y": 1, "confidence": 0.95},
                    ],
                }
            ),
        )
        res = predict_molscribe_full("/tmp/x.png")
        assert res["smiles"] == "CCO"
        assert res["low_confidence_atoms"] == [1]  # atom idx 1 is low
        assert res["atom_confidence_ok"] is False

    def test_all_high_confidence_ok(self, monkeypatch):
        from app.services.ocsr import predict_molscribe_full

        monkeypatch.setattr("app.services.ocsr.molscribe_available", lambda: True)
        monkeypatch.setattr(
            "app.services.ocsr._get_molscribe_model",
            lambda: _FakeMolscribeModel(
                {
                    "smiles": "CCO",
                    "confidence": 0.9,
                    "atoms": [
                        {"atom_symbol": "C", "x": 1, "y": 1, "confidence": 0.9},
                        {"atom_symbol": "O", "x": 2, "y": 1, "confidence": 0.95},
                    ],
                }
            ),
        )
        res = predict_molscribe_full("/tmp/x.png")
        assert res["atom_confidence_ok"] is True
        assert res["low_confidence_atoms"] == []

    def test_no_atoms_graceful(self, monkeypatch):
        from app.services.ocsr import predict_molscribe_full

        monkeypatch.setattr("app.services.ocsr.molscribe_available", lambda: True)
        monkeypatch.setattr(
            "app.services.ocsr._get_molscribe_model",
            lambda: _FakeMolscribeModel({"smiles": "CCO", "confidence": 0.8}),
        )
        res = predict_molscribe_full("/tmp/x.png")
        assert res["atoms"] is not None  # [] default
        assert res["atom_confidence_ok"] is True  # no atoms → vacuously ok


class _FakeMolscribeModel:
    """Stand-in for MolScribe model.predict_image_file."""

    def __init__(self, payload: dict):
        self._payload = payload

    def predict_image_file(self, image_file, return_confidence=False, return_atoms_bonds=False):
        return self._payload
