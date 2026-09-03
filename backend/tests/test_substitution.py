"""Material substitution: what could replace this, and what would it cost.

The point of this engine is the *predicted delta*, not similarity scoring, so
the tests pin the delta plumbing and — just as important — that the report is
honest about how much resolution the delta actually has in a given
environment.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import knowledge
from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.pipeline import reconstruct
from app.services.substitution import (
    find_substitutes,
    hansen_distance,
    scan_supply_risk,
    structural_score,
)

client = TestClient(app)


@pytest.fixture()
def material_store(tmp_path, monkeypatch):
    """Isolated material store so availability edits don't leak between tests."""
    import app.db.material_store as material_store_mod
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.material_store import MaterialStore

    engine = make_engine(f"sqlite:///{tmp_path}/substitution.db")
    Base.metadata.create_all(engine)
    store = MaterialStore(make_session_factory(engine))
    store.seed_missing(knowledge._SEED_MATERIALS)
    monkeypatch.setattr(material_store_mod, "_store", store)
    knowledge.RAW_MATERIALS.refresh()
    yield store
    knowledge.RAW_MATERIALS.refresh()


def _req(voc: float = 420) -> Requirement:
    return Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=voc)


def _genome(req: Requirement | None = None):
    return reconstruct.genome_from_requirement(req or _req())


def _slot_of(genome, role: str) -> int:
    return next(i for i, s in enumerate(genome.slots) if s.role == role)


# ── Hansen distance ──────────────────────────────────────────────────────────


def test_hansen_distance_weights_dispersion_by_four():
    a = {"hansen_d": 10.0, "hansen_p": 0.0, "hansen_h": 0.0}
    b = {"hansen_d": 11.0, "hansen_p": 0.0, "hansen_h": 0.0}
    assert hansen_distance(a, b) == pytest.approx(2.0)  # sqrt(4 * 1)


def test_hansen_distance_none_when_unparameterised():
    assert hansen_distance({"hansen_d": 1.0}, {"hansen_d": 2.0}) is None
    assert hansen_distance({}, {}) is None


def test_hansen_separates_water_from_xylene():
    """Water and xylene are the extremes of the catalog; the metric must say so."""
    water = knowledge.RAW_MATERIALS["Deionized water"]
    xylene = knowledge.RAW_MATERIALS["Xylene"]
    limonene = knowledge.RAW_MATERIALS["D-Limonene"]
    assert hansen_distance(water, xylene) > hansen_distance(xylene, limonene)


# ── structural scoring ───────────────────────────────────────────────────────


def test_same_substitute_group_scores_above_same_role_only():
    polyamide = knowledge.RAW_MATERIALS["Polyamide hardener"]
    ipda = knowledge.RAW_MATERIALS["Isophorone diamine (IPDA)"]
    desmodur = knowledge.RAW_MATERIALS["Desmodur BL 3175"]
    in_group, _ = structural_score(polyamide, ipda)
    cross_group, _ = structural_score(polyamide, desmodur)
    assert in_group > cross_group


def test_structural_score_reports_its_signals():
    a = knowledge.RAW_MATERIALS["Zinc phosphate"]
    b = knowledge.RAW_MATERIALS["Zinc molybdate"]
    score, breakdown = structural_score(a, b)
    assert 0.0 <= score <= 1.0
    assert breakdown["substitute_group"] == 1.0
    assert breakdown["functional_class"] == 0.0  # phosphate vs molybdate


def test_score_renormalises_over_available_signals():
    """A material without Hansen data must not be penalised against one with it."""
    bare_a = {"substitute_group": "g", "functional_class": "c"}
    bare_b = {"substitute_group": "g", "functional_class": "c"}
    score, _ = structural_score(bare_a, bare_b)
    assert score == pytest.approx(1.0)


def test_score_zero_when_nothing_comparable():
    assert structural_score({}, {})[0] == 0.0


# ── the deliverable: predicted deltas ────────────────────────────────────────


def test_reports_per_metric_delta_against_the_current_formulation():
    genome = _genome()
    report = find_substitutes(genome, _slot_of(genome, "hardener"), _req())
    assert report["original"] == "Polyamide hardener"
    assert report["candidates"], "no substitution candidates found"

    candidate = report["candidates"][0]
    cost = candidate["deltas"]["cost_cny_per_kg"]
    assert cost["before"] == pytest.approx(report["base_metrics"]["cost_cny_per_kg"])
    assert cost["delta"] == pytest.approx(cost["after"] - cost["before"], abs=0.01)
    assert cost["pct"] is not None


def test_delta_confidence_is_downgraded_without_rdkit():
    """Without molecular descriptors the predictor cannot tell two same-role
    materials apart on performance — only cost and VOC move. Reporting an
    unchanged salt-spray figure as if it were a finding would mislead."""
    from app.services import chemtools

    genome = _genome()
    report = find_substitutes(genome, _slot_of(genome, "hardener"), _req())
    expected = "high" if chemtools.availability().get("rdkit_installed") else "cost_only"
    assert all(c["delta_confidence"] == expected for c in report["candidates"])


def test_metric_present_on_only_one_side_is_not_a_delta_from_zero():
    from app.services.substitution import _metric_deltas

    out = _metric_deltas({"a": 1.0}, {"b": 2.0})
    assert out["a"] == {"before": 1.0, "after": None, "delta": None, "pct": None}
    assert out["b"]["before"] is None


# ── candidate recall ─────────────────────────────────────────────────────────


def test_recalls_by_substitute_group_not_merely_role():
    """Talc and fumed silica are both fillers, but one is a platy extender and
    the other a thixotrope — swapping them is not a substitution."""
    genome = _genome()
    index = next(i for i, s in enumerate(genome.slots) if s.material == "Talc")
    names = [c["material"] for c in find_substitutes(genome, index, _req())["candidates"]]
    assert "Fumed silica" not in names


def test_any_slot_can_be_queried_including_fixed_roles():
    """swappable() excludes carriers and fillers, but that constrains automated
    *search*; a user asking what replaces a pigment deserves an answer."""
    genome = _genome()
    index = next(i for i, s in enumerate(genome.slots) if s.role == "pigment")
    report = find_substitutes(genome, index, _req())
    assert report["original"] == "Titanium dioxide"


def test_discontinued_materials_are_not_proposed(material_store):
    genome = _genome()
    material_store.set_availability("Isophorone diamine (IPDA)", "discontinued")
    knowledge.RAW_MATERIALS.refresh()
    report = find_substitutes(genome, _slot_of(genome, "hardener"), _req())
    assert "Isophorone diamine (IPDA)" not in [c["material"] for c in report["candidates"]]


def test_include_unavailable_overrides(material_store):
    genome = _genome()
    material_store.set_availability("Isophorone diamine (IPDA)", "discontinued")
    knowledge.RAW_MATERIALS.refresh()
    report = find_substitutes(
        genome, _slot_of(genome, "hardener"), _req(), include_unavailable=True
    )
    assert "Isophorone diamine (IPDA)" in [c["material"] for c in report["candidates"]]


def test_out_of_range_slot_raises():
    with pytest.raises(IndexError):
        find_substitutes(_genome(), 999, _req())


# ── chemical feasibility is applied ──────────────────────────────────────────


def test_infeasible_replacements_rank_last_with_a_reason():
    """In a waterborne system a solvent-borne crosslinker must not top the list
    just because it looks structurally close."""
    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=100)
    genome = reconstruct.genome_from_requirement(req)
    report = find_substitutes(
        genome, _slot_of(genome, "hardener"), req, include_unavailable=True
    )
    feasible = [c["feasible"] for c in report["candidates"]]
    assert feasible == sorted(feasible, key=lambda ok: not ok)
    for candidate in report["candidates"]:
        if not candidate["feasible"]:
            assert candidate["blocking_reasons"]


# ── supply-risk scan ─────────────────────────────────────────────────────────


def test_supply_scan_finds_affected_formulations_and_suggests(material_store):
    material_store.set_availability("Zinc phosphate", "discontinued")
    knowledge.RAW_MATERIALS.refresh()
    scan = scan_supply_risk({"baseline": _genome()}, _req())

    assert scan["at_risk"]["Zinc phosphate"] == "discontinued"
    affected = scan["affected"]
    assert affected and affected[0]["formulation"] == "baseline"
    assert "Zinc phosphate" in [h["material"] for h in affected[0]["affected_slots"]]
    assert affected[0]["suggestions"]["Zinc phosphate"]


def test_supply_scan_clean_when_everything_in_stock():
    scan = scan_supply_risk({"baseline": _genome()}, _req())
    assert scan["at_risk"] == {}
    assert scan["affected"] == []


# ── endpoints ────────────────────────────────────────────────────────────────


def test_substitutes_endpoint_by_material_name():
    response = client.post(
        "/api/materials/substitutes",
        json={
            "requirement": _req().model_dump(mode="json"),
            "material": "Polyamide hardener",
            "limit": 5,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["original"] == "Polyamide hardener"
    assert body["substitute_group"] == "epoxy_hardener"


def test_substitutes_endpoint_requires_a_starting_point():
    assert client.post("/api/materials/substitutes", json={"material": "X"}).status_code == 400


def test_substitutes_endpoint_404_for_absent_material():
    response = client.post(
        "/api/materials/substitutes",
        json={
            "requirement": _req().model_dump(mode="json"),
            "material": "Not In Formulation",
        },
    )
    assert response.status_code == 404


def test_supply_risk_endpoint():
    response = client.get("/api/materials/supply-risk")
    assert response.status_code == 200
    assert "at_risk" in response.json()
