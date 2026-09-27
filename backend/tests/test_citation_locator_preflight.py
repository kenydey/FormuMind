"""Wave D — citation locator honesty in publication preflight."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.publication_preflight import run_checks


def test_locator_missing_warning():
    md = (
        "Cure at 120 °C for 2 h.[^1]\n\n"
        "[^1]: Smith 2023, epoxy coating study\n"
    )
    findings = run_checks(
        md, settings=SimpleNamespace(citation_locator_preflight="warning")
    )
    locs = [f for f in findings if f.check == "locator_missing"]
    assert len(locs) == 1
    assert locs[0].severity == "major"


def test_locator_present_passes():
    md = (
        "Cure at 120 °C for 2 h.[^1]\n\n"
        "[^1]: Source: doc.pdf, pp. 4, ¶2\n"
    )
    findings = run_checks(
        md, settings=SimpleNamespace(citation_locator_preflight="warning")
    )
    assert not [f for f in findings if f.check == "locator_missing"]


def test_locator_blocking_mode():
    md = "Adhesion 20 wt%.[^2]\n\n[^2]: Jones 2020\n"
    findings = run_checks(
        md, settings=SimpleNamespace(citation_locator_preflight="blocking")
    )
    locs = [f for f in findings if f.check == "locator_missing"]
    assert locs and locs[0].severity == "blocking"


def test_locator_off():
    md = "Adhesion 20 wt%.[^2]\n\n[^2]: Jones 2020\n"
    findings = run_checks(
        md, settings=SimpleNamespace(citation_locator_preflight="off")
    )
    assert not [f for f in findings if f.check == "locator_missing"]
