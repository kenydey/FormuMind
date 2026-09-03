"""End-to-end test: KG compatibility drives recommendation ranking order."""

from __future__ import annotations

import pytest

from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.services.recommend_pipeline import finalize_scored_formulations
from app.services.kg_chemical_check import ChemicalCheckResult
from app.services.kg_recommend_score import record_kg_compat


def _form(name: str, score: float, ingredients: list[str] | None = None) -> Formulation:
    if ingredients is None:
        ingredients = ["环氧树脂"]
    f = Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="resin", weight_pct=50.0) for n in ingredients],
    )
    f.score = score
    return f


def test_kg_incompatible_sinks_in_ranking(monkeypatch):
    """A KG-incompatible formulation ranks below an equal-score compatible one."""

    class _S:
        kg_enabled = True
        recommend_diversity_enabled = False
        recommend_default_n = 10
        recommend_max_n = 10
        recommend_diversity_lambda = 0.5

    monkeypatch.setattr("app.services.recommend_pipeline.get_settings", lambda: _S())

    good = _form("合规配方", 0.8, ingredients=["环氧树脂", "固化剂B"])
    bad = _form("不相容配方", 0.8, ingredients=["环氧树脂", "固化剂A"])  # same raw score, KG-incompatible
    record_kg_compat(
        bad,
        ChemicalCheckResult(
            feasible=False,
            status="infeasible",
            reasons=["A 与 B 不相容"],
            incompatible_pairs=[("A", "B", "inhibits")],
        ),
    )
    bad.score = 0.8 * 0.5  # kg_compat_adjust would have applied this penalty

    recs = []
    scored, formulas, _, _ = finalize_scored_formulations(recs, [good, bad], n=2)
    # The penalized (incompatible) formula must come after the good one.
    assert scored.index(good) < scored.index(bad)
    assert good.score >= bad.score


def test_kg_compat_field_survives_pipeline(monkeypatch):
    """kg_compat detail is preserved on the formulation through finalize."""

    class _S:
        recommend_diversity_enabled = False
        recommend_default_n = 10
        recommend_max_n = 10
        recommend_diversity_lambda = 0.5

    monkeypatch.setattr("app.services.recommend_pipeline.get_settings", lambda: _S())

    f = _form("X", 0.7)
    record_kg_compat(
        f,
        ChemicalCheckResult(
            feasible=True, status="pass", synergy_pairs=[("C", "D", "synergizes")]
        ),
    )
    scored, _, _, _ = finalize_scored_formulations([], [f], n=1)
    assert scored[0].kg_compat is not None
    assert scored[0].kg_compat["synergy_pairs"][0]["relation"] == "synergizes"


def test_score_and_validate_chem_screen_applies_kg(monkeypatch):
    """_score_and_validate(chem_screen=True) injects KG penalty via real path."""
    from app.pipeline.workflow import _score_and_validate, process_for
    from app.domain.schemas import Requirement, ObjectiveSpec

    class _S:
        kg_enabled = True
        kg_inhibits_penalty = 0.5
        kg_synergizes_bonus = 1.0

    monkeypatch.setattr("app.services.kg_recommend_score.get_settings", lambda: _S())
    monkeypatch.setattr("app.services.kg_chemical_check.get_settings", lambda: _S())
    monkeypatch.setattr(
        "app.services.kg_chemical_check._resolve_entity_id",
        lambda name: f"ent:{name}",
    )

    class _Rel:
        confidence = 0.9

        def __init__(self, other):
            self.relation_type = type("T", (), {"value": "inhibits"})()
            self.target_entity_id = other
            self.source_entity_id = "ent:X"
            self.evidence = [type("E", (), {"sentence": "文献报道两者不相容"})()]

    monkeypatch.setattr(
        "app.services.kg_chemical_check._incompatible_pairs_for",
        lambda eid: [("ent:固化剂A", _Rel("ent:固化剂A"), "文献报道两者不相容")]
        if eid == "ent:环氧树脂"
        else [],
    )
    monkeypatch.setattr("app.services.kg_chemical_check._synergy_pairs_for", lambda eid: [])

    def _fake_predict_full(form, process, req=None, **kw):
        form.predicted = {"corrosion_resistance": 0.8}
        form.predicted_std = {}
        return form.predicted, form.predicted_std

    monkeypatch.setattr("app.services.predictor.predict_full", _fake_predict_full)

    req = Requirement(
        domain="anticorrosion_coating",
        substrate="carbon_steel",
        objectives=[ObjectiveSpec(metric="corrosion_resistance", direction="maximize")],
    )
    form = _form("冲突配方", 1.0, ingredients=["环氧树脂", "固化剂A"])
    proc = process_for(req)
    out = _score_and_validate(form, proc, req, chem_screen=True)
    assert out.score is not None
    assert out.score < 1.0  # penalty applied
    assert out.kg_compat is not None
    assert out.kg_compat["feasible"] is False
    assert any("不相容" in w for w in out.warnings)
