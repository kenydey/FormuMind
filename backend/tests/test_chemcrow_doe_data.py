"""Phase D tests — versioned descriptor features, DOE chemistry review, and
formulation similarity dedup."""
from __future__ import annotations

import sys
import types

import pytest

from app.config import get_settings
from app.domain import features
from app.domain.schemas import (
    DOEFactor,
    DOEPlan,
    Formulation,
    Ingredient,
    ProductDomain,
    Requirement,
    Substrate,
)
from app.services import chemtools


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    chemtools.clear_cache()
    yield
    get_settings.cache_clear()
    chemtools.clear_cache()


def _install_fake_chemcrow(monkeypatch, **tool_outputs):
    tools_mod = types.ModuleType("chemcrow.tools")
    for name, output in tool_outputs.items():
        def make_cls(out):
            class _Tool:
                def _run(self, arg):
                    return out(arg) if callable(out) else out

            return _Tool

        setattr(tools_mod, name, make_cls(output))
    pkg = types.ModuleType("chemcrow")
    pkg.tools = tools_mod
    monkeypatch.setitem(sys.modules, "chemcrow", pkg)
    monkeypatch.setitem(sys.modules, "chemcrow.tools", tools_mod)


def _form(name: str, *ings: Ingredient) -> Formulation:
    return Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=list(ings),
        rationale="t",
    )


# ── versioned descriptor features ────────────────────────────────────────────


def test_v1_feature_set_is_default_and_stable():
    assert features.feature_set_version() == "v1"
    assert features.active_feature_keys() == features.FEATURE_KEYS
    form = _form("f", Ingredient(name="r", role="resin", weight_pct=60.0))
    vec = features.vector(form)
    assert len(vec) == len(features.FEATURE_KEYS)
    feats = features.featurize(form)
    assert not any(k.startswith("desc_") for k in feats)


def test_v2_feature_set_appends_descriptor_keys(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_DESCRIPTOR_FEATURES", "true")
    get_settings.cache_clear()
    assert features.feature_set_version() == "v2-desc"
    assert features.active_feature_keys() == features.FEATURE_KEYS + features.DESCRIPTOR_KEYS
    # v1 keys untouched — stored v1 models remain interpretable
    assert "desc_mol_wt" not in features.FEATURE_KEYS


def test_v2_vector_aligned_even_when_descriptors_unresolvable(monkeypatch):
    """Without rdkit every descriptor is 0.0 but vector length stays fixed."""
    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_DESCRIPTOR_FEATURES", "true")
    get_settings.cache_clear()
    form = _form("f", Ingredient(name="r", role="resin", weight_pct=60.0, smiles="CCO"))
    vec = features.vector(form)
    assert len(vec) == len(features.FEATURE_KEYS) + len(features.DESCRIPTOR_KEYS)
    if not chemtools.rdkit_available():
        assert vec[-len(features.DESCRIPTOR_KEYS):] == [0.0] * len(features.DESCRIPTOR_KEYS)


def test_v2_descriptor_block_weight_averages(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_DESCRIPTOR_FEATURES", "true")
    get_settings.cache_clear()

    def fake_desc(smiles):
        return {
            "CCO": {"mol_wt": 46.0, "logp": -0.3, "tpsa": 20.0, "hbd": 1.0, "hba": 1.0, "arom_rings": 0.0},
            "c1ccccc1": {"mol_wt": 78.0, "logp": 2.0, "tpsa": 0.0, "hbd": 0.0, "hba": 0.0, "arom_rings": 1.0},
        }.get(smiles)

    monkeypatch.setattr(chemtools, "mol_descriptors", fake_desc)
    form = _form(
        "f",
        Ingredient(name="a", role="resin", weight_pct=75.0, smiles="CCO"),
        Ingredient(name="b", role="solvent", weight_pct=25.0, smiles="c1ccccc1"),
        Ingredient(name="c", role="additive", weight_pct=5.0),  # no smiles -> excluded
    )
    feats = features.featurize(form)
    assert feats["desc_mol_wt"] == pytest.approx(46 * 0.75 + 78 * 0.25)
    assert feats["desc_arom_rings"] == pytest.approx(0.25)


# ── DOE chemistry review ─────────────────────────────────────────────────────


def _plan(*factor_names: str) -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[DOEFactor(name=n, low=0.0, high=1.0) for n in factor_names],
        runs=[],
    )


def test_review_doe_empty_offline():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[{"name": "DGEBA", "role": "resin", "smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"}],
    )
    if not chemtools.rdkit_available():
        assert chemtools.review_doe_factors(req, _plan("resin")) == []


def test_review_doe_flags_reactive_pair_without_cure_factor(monkeypatch):
    def fake_groups(smiles):
        return {
            "EPOXY": "This molecule contains epoxide groups.",
            "AMINE": "This molecule contains primary amine groups.",
        }.get(smiles, "This molecule contains ether groups.")

    _install_fake_chemcrow(monkeypatch, FuncGroups=fake_groups, ControlChemCheck="not found")
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[
            {"name": "resin", "role": "resin", "smiles": "EPOXY"},
            {"name": "hardener", "role": "hardener", "smiles": "AMINE"},
        ],
    )
    notes = chemtools.review_doe_factors(req, _plan("resin", "hardener"))
    assert any("固化温度" in n for n in notes)
    # With a cure factor present the note disappears
    notes2 = chemtools.review_doe_factors(req, _plan("resin", "cure_temperature_c"))
    assert not any("固化温度" in n for n in notes2)


def test_review_doe_flags_controlled_material(monkeypatch):
    _install_fake_chemcrow(
        monkeypatch,
        FuncGroups="This molecule contains ether groups.",
        ControlChemCheck="appears in a list of controlled chemicals",
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate=Substrate.carbon_steel,
        materials=[{"name": "solventX", "role": "solvent", "smiles": "CCO"}],
    )
    notes = chemtools.review_doe_factors(req, _plan("solvent"))
    assert any("管制" in n for n in notes)


def test_build_doe_appends_review_notes(monkeypatch):
    from app.pipeline import workflow

    monkeypatch.setattr(
        chemtools, "review_doe_factors", lambda req, plan: ["化学审查：测试提示"]
    )
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating, substrate=Substrate.carbon_steel
    )
    plan = workflow.build_doe(req, design="lhs", n=4)
    assert "化学审查：测试提示" in plan.notes


# ── similarity dedup ─────────────────────────────────────────────────────────


def test_dedupe_keeps_distinct_formulations():
    a = _form("A", Ingredient(name="epoxy", role="resin", weight_pct=60.0))
    b = _form("B", Ingredient(name="acrylic", role="resin", weight_pct=60.0))
    kept, notes = chemtools.dedupe_similar_formulations([a, b])
    assert kept == [a, b]
    assert notes == []


def test_dedupe_drops_name_identical_composition():
    a = _form(
        "A",
        Ingredient(name="epoxy", role="resin", weight_pct=60.0),
        Ingredient(name="ipda", role="hardener", weight_pct=20.0),
    )
    b = _form(
        "A'",
        Ingredient(name="epoxy", role="resin", weight_pct=60.5),
        Ingredient(name="ipda", role="hardener", weight_pct=20.0),
    )
    kept, notes = chemtools.dedupe_similar_formulations([a, b])
    assert kept == [a]
    assert notes and "已去重" in notes[0]


def test_dedupe_respects_weight_differences():
    """Same materials at very different loadings are distinct designs."""
    a = _form("A", Ingredient(name="epoxy", role="resin", weight_pct=60.0))
    b = _form("B", Ingredient(name="epoxy", role="resin", weight_pct=30.0))
    kept, _ = chemtools.dedupe_similar_formulations([a, b])
    assert kept == [a, b]


def test_dedupe_disabled_gateway_is_noop(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CHEMTOOLS_ENABLED", "false")
    get_settings.cache_clear()
    a = _form("A", Ingredient(name="epoxy", role="resin", weight_pct=60.0))
    b = _form("A'", Ingredient(name="epoxy", role="resin", weight_pct=60.0))
    kept, notes = chemtools.dedupe_similar_formulations([a, b])
    assert kept == [a, b]
    assert notes == []


def test_formulation_similarity_uses_mol_similarity(monkeypatch):
    monkeypatch.setattr(chemtools, "mol_similarity", lambda x, y: 0.99)
    a = _form("A", Ingredient(name="x1", role="resin", weight_pct=50.0, smiles="S1"))
    b = _form("B", Ingredient(name="x2", role="resin", weight_pct=50.0, smiles="S2"))
    assert chemtools.formulation_similarity(a, b) == pytest.approx(0.99)
