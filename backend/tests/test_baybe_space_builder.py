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
