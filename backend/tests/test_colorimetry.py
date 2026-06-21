"""Tests for the colorimetry service — offline fallback + CIE76 ΔE."""
import pytest

from app.domain import knowledge
from app.domain.schemas import ProductDomain, Requirement
from app.services.colorimetry import (
    WHITE_LAB,
    _colour_available,
    color_metrics,
    delta_e_2000,
    mixture_lab,
)


def test_delta_e_identical_is_zero():
    assert delta_e_2000((50.0, 20.0, -10.0), (50.0, 20.0, -10.0)) == pytest.approx(0.0, abs=1e-6)


def test_delta_e_white_vs_black_large():
    de = delta_e_2000(WHITE_LAB, (0.0, 0.0, 0.0))
    assert de > 50.0, f"Expected large ΔE, got {de}"


def test_delta_e_symmetry():
    a, b = (80.0, 5.0, -3.0), (70.0, -2.0, 10.0)
    assert delta_e_2000(a, b) == pytest.approx(delta_e_2000(b, a), abs=0.01)


def test_mixture_lab_pigmented_formula():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    lab = mixture_lab(form)
    assert lab is not None, "Expected Lab value for pigmented primer"
    L, _, _ = lab
    assert 80.0 < L <= 100.0, f"Bright white primer L* should be high, got {L}"


def test_mixture_lab_pigment_free_is_none():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    assert mixture_lab(form) is None


def test_color_metrics_pigmented_formula_has_delta_e():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    metrics = color_metrics(form)
    assert "delta_e" in metrics
    assert "lab_L" in metrics and "lab_a" in metrics and "lab_b" in metrics
    assert metrics["delta_e"] >= 0.0


def test_color_metrics_empty_for_pigment_free():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    assert color_metrics(form) == {}


def test_colour_available_is_bool():
    assert isinstance(_colour_available(), bool)
