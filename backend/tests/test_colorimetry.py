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


# ── the optional colour-science dependency ──────────────────────────────────

def test_colour_availability_is_probed_once_and_logs_nothing(caplog, monkeypatch):
    """A default install has no colour-science. ``predict()`` reaches this on every candidate of an optimisation
    run, and the probe used to import-and-fail (logging a WARNING "optional feature check: No module named
    'colour'") each time — one log line per prediction."""
    import logging

    import app.services.colorimetry as colorimetry

    probes: list[str] = []
    monkeypatch.setattr(colorimetry, "optional_import", lambda name: probes.append(name) or False)
    colorimetry._colour_available.cache_clear()
    try:
        with caplog.at_level(logging.DEBUG):
            for _ in range(50):
                colorimetry.delta_e_2000((50.0, 0.0, 0.0), (60.0, 5.0, 5.0))
        assert probes == ["colour"], "probed more than once"
        assert not [r for r in caplog.records if "optional feature check" in r.getMessage()]
    finally:
        colorimetry._colour_available.cache_clear()


def test_without_colour_science_the_metric_is_cie76(monkeypatch):
    import app.services.colorimetry as colorimetry

    monkeypatch.setattr(colorimetry, "_colour_available", lambda: False)
    # CIE76 is the Euclidean distance in L*a*b*
    assert colorimetry.delta_e_2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(4.0, abs=0.01)


def test_ciede2000_matches_the_published_test_pair():
    """The branch that uses colour-science is otherwise never run (CI installs no ``color`` extra).
    Sharma, Wu & Dalal (2005), pair 1: ΔE00 = 2.0425."""
    pytest.importorskip("colour")
    import app.services.colorimetry as colorimetry

    colorimetry._colour_available.cache_clear()
    assert colorimetry._colour_available()
    assert delta_e_2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(2.0425, abs=1e-3)
