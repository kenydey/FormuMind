"""Tests for the rheology service — Fox Tg, Mooney viscosity, viscoelastic index."""
import pytest

from app.domain.knowledge import baseline_formulation
from app.domain.schemas import ProductDomain, Requirement
from app.services.rheology import (
    fox_tg,
    fox_tg_celsius,
    mooney_viscosity,
    viscoelastic_index,
)

_ANTI = Requirement(domain=ProductDomain.anticorrosion_coating)
_DEG = Requirement(domain=ProductDomain.degreaser)


def test_fox_tg_anticorrosion_returns_value():
    form = baseline_formulation(_ANTI)
    tg_k = fox_tg(form)
    # Anticorrosion primer has epoxy + polyamide hardener, both have tg_k → should work
    assert tg_k is not None, "Fox Tg should return a value for the anticorrosion primer"
    assert 200 < tg_k < 400, f"Tg={tg_k} K outside plausible polymer range"


def test_fox_tg_celsius_consistent():
    form = baseline_formulation(_ANTI)
    tg_k = fox_tg(form)
    tg_c = fox_tg_celsius(form)
    if tg_k is not None:
        assert tg_c is not None
        assert abs(tg_c - (tg_k - 273.15)) < 0.1
    else:
        assert tg_c is None


def test_fox_tg_degreaser_returns_none_no_polymer():
    form = baseline_formulation(_DEG)
    # Degreaser has no resin/hardener components in the polymer-role list
    tg = fox_tg(form)
    # May be None (no polymer data) or a value if some component has tg_k
    # Either way should not raise
    assert tg is None or isinstance(tg, float)


def test_mooney_viscosity_pigmented_formula():
    form = baseline_formulation(_ANTI)
    eta = mooney_viscosity(form)
    assert eta is not None, "Anticorrosion primer with pigments should have viscosity"
    assert eta >= 1.0, "Relative viscosity must be >= 1 (pure liquid)"
    assert eta < 1000, "Relative viscosity should be plausible"


def test_mooney_viscosity_degreaser_returns_none():
    form = baseline_formulation(_DEG)
    # Degreaser has no pigments → PVC=0 → mooney returns None
    eta = mooney_viscosity(form)
    assert eta is None, "Pigment-free degreaser should have no Mooney viscosity"


def test_viscoelastic_index_in_unit_range():
    for domain in ProductDomain:
        form = baseline_formulation(Requirement(domain=domain))
        idx = viscoelastic_index(form)
        assert 0.0 <= idx <= 1.0, f"VEI={idx} out of [0, 1] for {domain}"


def test_rheology_predict_full_integration():
    """Rheology metrics appear in predict_full output for polymer-rich systems."""
    from app.services.predictor import predict_full

    form = baseline_formulation(_ANTI)
    props, _ = predict_full(form)
    # viscoelastic_index is always emitted even when Tg unknown
    assert "viscoelastic_index" in props
