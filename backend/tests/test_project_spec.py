import pytest

from app.domain.examples import load_example
from app.domain.project_spec import (
    effective_project_id,
    levers_to_doe_factors,
    normalize_constraints,
    normalize_requirement,
    primary_objective,
    resolve_levers,
)
from app.domain.schemas import LeverSpec, ProductDomain, Requirement, Substrate
from app.pipeline import reconstruct


def test_effective_project_id_prefers_explicit():
    req = Requirement(domain=ProductDomain.degreaser, project_id="my_project")
    assert effective_project_id(req) == "my_project"


def test_effective_project_id_from_product_type():
    req = Requirement(domain=ProductDomain.degreaser, product_type="Industrial Cleaner X")
    assert effective_project_id(req) == "industrial_cleaner_x"


def test_normalize_fills_product_type_and_application():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, substrate=Substrate.carbon_steel)
    norm = normalize_requirement(req)
    assert norm.product_type
    assert norm.application == "carbon_steel"
    assert norm.project_id


def test_primary_objective_from_objectives_list():
    req = load_example("degreaser")
    assert primary_objective(req) == "cleaning_efficiency"


def test_resolve_levers_explicit_over_legacy():
    req = load_example("anticorrosion_coating")
    levers = resolve_levers(req)
    assert levers[0].name == "Zinc phosphate"
    factors = levers_to_doe_factors(levers)
    assert factors[0].low == pytest.approx(2.0)


def test_resolve_levers_from_formulation_when_no_explicit():
    form = reconstruct.formulation_from_factors(
        ProductDomain.degreaser,
        {"Nonionic surfactant (C12-14 EO7)": 6.0, "Sodium metasilicate": 8.0},
    )
    req = Requirement(domain=ProductDomain.degreaser, levers=[])
    levers = resolve_levers(req, form=form)
    assert any(l.name == "Nonionic surfactant (C12-14 EO7)" for l in levers)


def test_load_example_unknown_raises():
    with pytest.raises(KeyError):
        load_example("nonexistent")


def test_normalize_constraints_includes_voc():
    req = load_example("anticorrosion_coating")
    merged = normalize_constraints(req)
    assert "VOC 上限" in merged
