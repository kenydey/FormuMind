"""Tests for BayBE search space constraint propagation."""
from __future__ import annotations

from app.domain.schemas import DOEFactor, ProductDomain, Requirement
from app.services.engines.adapters.baybe_space_builder import apply_requirement_bounds


def test_apply_requirement_bounds_caps_cure_temperature():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=90)
    factors = [
        DOEFactor(name="Cure temperature", low=60.0, high=120.0, unit="°C"),
    ]
    adjusted = apply_requirement_bounds(req, factors)
    assert adjusted[0].high == 90.0


def test_apply_requirement_bounds_narrows_ph():
    req = Requirement(domain=ProductDomain.degreaser, ph_target=9.0)
    factors = [DOEFactor(name="pH", low=6.0, high=12.0, unit="")]
    adjusted = apply_requirement_bounds(req, factors)
    assert adjusted[0].low >= 7.5
    assert adjusted[0].high <= 10.5


def test_apply_requirement_bounds_voc_factor():
    req = Requirement(domain=ProductDomain.degreaser, voc_limit_gpl=350)
    factors = [DOEFactor(name="VOC level", low=100.0, high=500.0, unit="g/L")]
    adjusted = apply_requirement_bounds(req, factors)
    assert adjusted[0].high <= 350.0


def test_ph_target_does_not_clamp_ingredient_factors():
    """Regression: the pH/VOC/cure keyword tests are substring matches, and
    "ph" occurs inside several real material names. A pH target constrains the
    bath, never a component's dosage."""
    req = Requirement(domain=ProductDomain.surface_treatment, ph_target=3.0)
    factors = [
        DOEFactor(name="Phosphoric acid", low=3.0, high=14.0, unit="wt%"),
        DOEFactor(name="Zinc phosphate", low=2.0, high=14.0, unit="wt%"),
        DOEFactor(name="Manganese dihydrogen phosphate", low=1.0, high=8.0, unit="wt%"),
        DOEFactor(name="Bisphenol-A epoxy (DGEBA)", low=28.0, high=48.0, unit="wt%"),
    ]
    adjusted = apply_requirement_bounds(req, factors)
    for before, after in zip(factors, adjusted):
        assert (after.low, after.high) == (before.low, before.high), before.name


def test_voc_limit_does_not_clamp_ingredient_factors():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=120)
    factors = [DOEFactor(name="Bisphenol-A epoxy (DGEBA)", low=28.0, high=48.0, unit="wt%")]
    adjusted = apply_requirement_bounds(req, factors)
    assert adjusted[0].high == 48.0


def test_process_knobs_are_still_clamped():
    """The guard must not disable the constraint for genuine process factors."""
    req = Requirement(domain=ProductDomain.surface_treatment, ph_target=9.0)
    adjusted = apply_requirement_bounds(
        req, [DOEFactor(name="bath_ph", low=2.0, high=13.0, unit="")]
    )
    assert adjusted[0].low == 7.5 and adjusted[0].high == 10.5
