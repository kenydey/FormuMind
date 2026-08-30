"""Autodeposition domain tests — intent detection, template, materials, corpus."""

import pytest

from app.domain.examples import EXAMPLE_PROJECTS
from app.domain.knowledge import (
    MECHANISMS,
    RAW_MATERIALS,
    TEMPLATE_BUILDERS,
    baseline_formulation,
)
from app.domain.schemas import ProductDomain, Requirement, Substrate
from app.services.intent import _detect_domain
from app.services.literature import SEED_CORPUS


def _autodep_req(*, salt_spray_hours: float = 500.0, ph_target: float | None = 3.0) -> Requirement:
    return Requirement(
        domain=ProductDomain.autodeposition_coating,
        substrate=Substrate.carbon_steel,
        salt_spray_hours=salt_spray_hours,
        ph_target=ph_target,
    )


# ── Intent detection ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "自沉积涂料 铁基底 pH 3 耐盐雾 500h",
        "autodeposition coating for steel",
        "autophoretic coating bath development",
        "BONDERITE M-PP 自泳漆对标",
    ],
)
def test_intent_detects_autodeposition(text):
    assert _detect_domain(text) == ProductDomain.autodeposition_coating


@pytest.mark.parametrize(
    "text",
    [
        "防腐涂料 环氧底漆",  # generic coating must NOT be hijacked
        "脱脂剂 碱性清洗",
        "磷化 转化膜",
    ],
)
def test_intent_does_not_hijack_other_domains(text):
    detected = _detect_domain(text)
    assert detected != ProductDomain.autodeposition_coating


# ── Template ────────────────────────────────────────────────────────────────

def test_autodeposition_template_registered():
    assert ProductDomain.autodeposition_coating in TEMPLATE_BUILDERS


def test_autodeposition_template_closure_and_roles():
    form = baseline_formulation(_autodep_req())
    assert form.domain == ProductDomain.autodeposition_coating
    assert abs(sum(i.weight_pct for i in form.ingredients) - 100.0) < 0.01
    roles = {i.role for i in form.ingredients}
    assert "resin" in roles and "active" in roles and "accelerator" in roles
    names = {i.name for i in form.ingredients}
    # Core autodeposition skeleton: acid-tolerant binder + Fe³⁺ promoter + oxidizer
    assert "Acidic-stable epoxy-acrylic emulsion" in names
    assert "Ferric fluoride (FeF3)" in names
    assert "Hydrogen peroxide (H2O2)" in names


def test_autodeposition_template_scales_promoter_with_salt_spray():
    low = baseline_formulation(_autodep_req(salt_spray_hours=240))
    high = baseline_formulation(_autodep_req(salt_spray_hours=720))
    low_pct = next(i.weight_pct for i in low.ingredients if i.name == "Ferric fluoride (FeF3)")
    high_pct = next(i.weight_pct for i in high.ingredients if i.name == "Ferric fluoride (FeF3)")
    assert high_pct > low_pct


def test_autodeposition_mechanism_documented():
    text = MECHANISMS[ProductDomain.autodeposition_coating]
    assert "coagulation" in text.lower() and "interface" in text.lower()


# ── Materials ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "name",
    [
        "Acidic-stable epoxy-acrylic emulsion",
        "Cationic polyurethane dispersion (acid-stable)",
        "Ferric fluoride (FeF3)",
        "Hydrofluoric acid (HF)",
        "Hydrogen peroxide (H2O2)",
        "Citric acid",
        "Carbon black",
        "Sodium bifluoride (NaHF2)",
    ],
)
def test_autodeposition_materials_present(name):
    assert name in RAW_MATERIALS


def test_acidic_emulsion_has_acid_tolerance():
    spec = RAW_MATERIALS["Acidic-stable epoxy-acrylic emulsion"]
    assert spec.get("carrier") == "aqueous"
    assert spec.get("acid_tolerance_ph") is not None
    assert spec.get("acid_tolerance_ph") <= 3.0  # survives pH 2-4 working window


def test_feF3_and_hf_are_active_with_cas():
    for name, cas in [("Ferric fluoride (FeF3)", "7783-50-8"), ("Hydrofluoric acid (HF)", "7664-39-3")]:
        spec = RAW_MATERIALS[name]
        assert spec.get("role") == "active"
        assert spec.get("cas_no") == cas


# ── Corpus ──────────────────────────────────────────────────────────────────

def test_autodeposition_seed_corpus_present():
    docs = SEED_CORPUS[ProductDomain.autodeposition_coating]
    assert len(docs) >= 3
    identifiers = {d["identifier"] for d in docs}
    assert "BONDERITE-M-PP-866R" in identifiers
    assert "WO2017117169A1" in identifiers


def test_autodeposition_example_project_present():
    assert "autodeposition_coating" in EXAMPLE_PROJECTS
    proj = EXAMPLE_PROJECTS["autodeposition_coating"]
    assert proj["domain"] == ProductDomain.autodeposition_coating
    lever_names = {l.name for l in proj["levers"]}
    assert "Ferric fluoride (FeF3)" in lever_names
