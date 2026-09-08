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


@pytest.fixture(autouse=True)
def _disable_external_network(monkeypatch):
    """Catalog substitution tests must not hit PubChem."""
    monkeypatch.setattr(
        "app.services.external_alternatives.external_substitutes_enabled",
        lambda: False,
    )


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


def test_uncatalogued_slot_material_still_returns_report():
    """LLM/recommend formulas often name materials not yet in RAW_MATERIALS.

    Substitution must tolerate that (strict=False) and still rank same-role
    catalog replacements + optional external lookup.
    """
    from app.domain.genome import FormulationGenome, Slot

    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[
            Slot(role="resin", material="Bisphenol-A epoxy (DGEBA)", weight_pct=55.0),
            Slot(role="hardener", material="Polyamide hardener", weight_pct=20.0),
            Slot(
                role="inhibitor",
                material="Cerium nitrate hexahydrate",
                weight_pct=5.0,
            ),
            Slot(role="solvent", material="Xylene", weight_pct=20.0),
        ],
    )
    assert "Cerium nitrate hexahydrate" not in knowledge.RAW_MATERIALS
    idx = next(i for i, s in enumerate(genome.slots) if s.material == "Cerium nitrate hexahydrate")
    report = find_substitutes(
        genome, idx, _req(), include_external=False, limit=10
    )
    assert report["original"] == "Cerium nitrate hexahydrate"
    assert report["role"] == "inhibitor"
    assert report["candidates"], "expected same-role catalog inhibitors"
    assert all(c["role"] == "inhibitor" for c in report["candidates"] if c.get("role"))
    # Reconstruct must not raise; identity still present when external off.
    assert report["identity"]["query"] == "Cerium nitrate hexahydrate"


def test_substitutes_endpoint_accepts_uncatalogued_formulation_slot():
    response = client.post(
        "/api/materials/substitutes",
        json={
            "formulation": {
                "name": "Ce formula",
                "domain": "anticorrosion_coating",
                "ingredients": [
                    {
                        "name": "Bisphenol-A epoxy (DGEBA)",
                        "role": "resin",
                        "weight_pct": 60.0,
                    },
                    {
                        "name": "Cerium nitrate hexahydrate",
                        "role": "inhibitor",
                        "weight_pct": 5.0,
                    },
                    {"name": "Xylene", "role": "solvent", "weight_pct": 35.0},
                ],
            },
            "material": "Cerium nitrate hexahydrate",
            "limit": 5,
            "include_external": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["original"] == "Cerium nitrate hexahydrate"
    assert body["candidates"]


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
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert "message" in detail
    assert "candidates" in detail
    assert isinstance(detail["candidates"], list)
    assert detail["candidates"], "404 should list slot material names"
    assert "Polyamide hardener" in detail["candidates"]


def test_substitutes_endpoint_case_insensitive_material_match():
    response = client.post(
        "/api/materials/substitutes",
        json={
            "requirement": _req().model_dump(mode="json"),
            "material": "polyamide HARDENER",
            "limit": 3,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["original"] == "Polyamide hardener"


def test_substitutes_endpoint_fuzzy_substring_material_match():
    """Partial name that uniquely identifies a slot should resolve."""
    response = client.post(
        "/api/materials/substitutes",
        json={
            "requirement": _req().model_dump(mode="json"),
            "material": "polyamide",
            "limit": 3,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["original"] == "Polyamide hardener"


def test_substitutes_endpoint_ambiguous_fuzzy_returns_candidates():
    """A query matching multiple slots must not silently pick one."""
    # Anticorrosion baseline includes both "Deionized water" and often other
    # names containing overlapping tokens; use a formulation with two clear hits.
    response = client.post(
        "/api/materials/substitutes",
        json={
            "formulation": {
                "name": "Ambiguous",
                "domain": "anticorrosion_coating",
                "ingredients": [
                    {
                        "name": "Waterborne acrylic emulsion",
                        "role": "resin",
                        "weight_pct": 60.0,
                    },
                    {"name": "Deionized water", "role": "solvent", "weight_pct": 40.0},
                ],
            },
            "material": "water",
            "limit": 3,
        },
    )
    assert response.status_code == 404, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert set(detail["candidates"]) == {
        "Waterborne acrylic emulsion",
        "Deionized water",
    }


def test_supply_risk_endpoint():
    response = client.get("/api/materials/supply-risk")
    assert response.status_code == 200
    assert "at_risk" in response.json()


# ── external (PubChem) channel ───────────────────────────────────────────────


def test_include_external_false_returns_empty_external():
    genome = _genome()
    report = find_substitutes(
        genome, _slot_of(genome, "hardener"), _req(), include_external=False
    )
    assert report["external"] == []
    assert report["external_meta"]["skipped_reason"] == "include_external=false"
    assert "identity" in report


def test_external_channel_merges_mocked_pubchem(monkeypatch):
    monkeypatch.setattr(
        "app.services.external_alternatives.external_substitutes_enabled",
        lambda: True,
    )

    def _fake_fetch(**kwargs):
        return {
            "identity": {
                "query": kwargs["material"],
                "cas_no": "999-99-9",
                "smiles": "CCO",
                "cid": None,
                "source": "catalog",
                "resolved": True,
            },
            "external": [
                {
                    "name": "Fake Ext Alcohol",
                    "iupac_name": "ethanol",
                    "cas_no": "64-17-5",
                    "smiles": "CCO",
                    "cid": 702,
                    "formula": "C2H6O",
                    "molar_mass": 46.07,
                    "similarity": 0.9,
                    "source": "pubchem_similar",
                    "in_catalog": False,
                    "catalog_name": None,
                    "role_hint": "hardener",
                    "note": "结构相似；未做配方 Δ 预测（入库后可再算）",
                }
            ],
            "external_meta": {
                "enabled": True,
                "queried": True,
                "count": 1,
                "skipped_reason": None,
                "provider": "pubchem_fastsimilarity_2d",
            },
        }

    monkeypatch.setattr(
        "app.services.external_alternatives.fetch_external_alternatives", _fake_fetch
    )
    genome = _genome()
    report = find_substitutes(
        genome, _slot_of(genome, "hardener"), _req(), include_external=True
    )
    assert len(report["external"]) == 1
    assert report["external"][0]["name"] == "Fake Ext Alcohol"
    assert report["identity"]["resolved"] is True


def test_include_literature_false_returns_empty_literature():
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=False,
        include_literature=False,
    )
    assert report["literature"] == []
    assert report["literature_meta"]["skipped_reason"] == "include_literature=false"
    assert "literature" not in report["layers_used"]
    assert "catalog" in report["layers_used"]


def test_literature_channel_merges_mocked_kg_kb(monkeypatch):
    def _fake_lit(**kwargs):
        return {
            "literature": [
                {
                    "name": "Fake Lit Hardener",
                    "source": "kg",
                    "confidence": 0.8,
                    "entity_id": "chem:fake",
                    "cas_no": None,
                    "smiles": None,
                    "role_hint": "hardener",
                    "in_catalog": False,
                    "catalog_name": None,
                    "evidence": [
                        {
                            "source_id": "doi:10.0/fake",
                            "chunk_id": None,
                            "sentence": "Fake Lit Hardener substitutes polyamide.",
                            "confidence": 0.7,
                        }
                    ],
                    "note": "知识图谱 substitutes 边",
                }
            ],
            "literature_meta": {
                "enabled": True,
                "queried": True,
                "count": 1,
                "skipped_reason": None,
                "providers": ["kg"],
            },
        }

    monkeypatch.setattr(
        "app.services.literature_alternatives.fetch_literature_alternatives",
        _fake_lit,
    )
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=False,
        include_literature=True,
    )
    assert len(report["literature"]) == 1
    assert report["literature"][0]["name"] == "Fake Lit Hardener"
    assert "literature" in report["layers_used"]
    assert report["original_in_catalog"] is True


def test_literature_failure_degrades_without_500(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("kg down")

    monkeypatch.setattr(
        "app.services.literature_alternatives.fetch_literature_alternatives",
        _boom,
    )
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=False,
        include_literature=True,
    )
    assert report["candidates"]  # catalog still present
    assert report["literature"] == []
    assert "literature_error" in (report["literature_meta"]["skipped_reason"] or "")


def test_requirement_fit_prefers_voc_reduction():
    from app.services.substitution import _requirement_fit

    req = _req(voc=350)
    better = {
        "deltas": {
            "voc_gpl": {"pct": -20.0},
            "salt_spray_hours": {"pct": 0.0},
            "cost_cny_per_kg": {"pct": 5.0},
        }
    }
    worse = {
        "deltas": {
            "voc_gpl": {"pct": 10.0},
            "salt_spray_hours": {"pct": 0.0},
            "cost_cny_per_kg": {"pct": -5.0},
        }
    }
    assert _requirement_fit(better, req) > _requirement_fit(worse, req)


def test_uncatalogued_slot_still_returns_literature_keys(monkeypatch):
    from app.domain.genome import FormulationGenome, Slot

    monkeypatch.setattr(
        "app.services.literature_alternatives.fetch_literature_alternatives",
        lambda **kwargs: {
            "literature": [
                {
                    "name": "Cerium nitrate",
                    "source": "kb_product",
                    "confidence": 0.55,
                    "entity_id": None,
                    "cas_no": None,
                    "smiles": None,
                    "role_hint": "inhibitor",
                    "in_catalog": True,
                    "catalog_name": "Cerium nitrate",
                    "evidence": [],
                    "note": "KB 产品登记簿",
                }
            ],
            "literature_meta": {
                "enabled": True,
                "queried": True,
                "count": 1,
                "skipped_reason": None,
                "providers": ["kb_product"],
            },
        },
    )
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[
            Slot(role="resin", material="Bisphenol-A epoxy (DGEBA)", weight_pct=40),
            Slot(role="hardener", material="Polyamide hardener", weight_pct=14),
            Slot(role="inhibitor", material="Cerium nitrate hexahydrate", weight_pct=3),
            Slot(role="solvent", material="Deionized water", weight_pct=43),
        ],
    )
    report = find_substitutes(
        genome, 2, _req(), include_external=False, include_literature=True, limit=10
    )
    assert report["original"] == "Cerium nitrate hexahydrate"
    assert report["original_in_catalog"] is False
    assert "literature" in report
    assert report["literature"][0]["name"] == "Cerium nitrate"
    assert "literature" in report["layers_used"]


def test_substitutes_endpoint_default_include_literature_true(monkeypatch):
    monkeypatch.setattr(
        "app.services.literature_alternatives.fetch_literature_alternatives",
        lambda **kwargs: {
            "literature": [],
            "literature_meta": {
                "enabled": True,
                "queried": True,
                "count": 0,
                "skipped_reason": "无文献/产品替代命中",
                "providers": [],
            },
        },
    )
    response = client.post(
        "/api/materials/substitutes",
        json={
            "requirement": {"domain": "anticorrosion_coating", "voc_limit_gpl": 420},
            "material": "Polyamide hardener",
            "limit": 3,
            "include_external": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "literature" in body
    assert "literature_meta" in body
    assert "layers_used" in body
    assert "original_in_catalog" in body
    assert body["literature_meta"]["enabled"] is True


def test_substitutes_endpoint_default_include_external_true(monkeypatch):
    monkeypatch.setattr(
        "app.services.external_alternatives.external_substitutes_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.external_alternatives.fetch_external_alternatives",
        lambda **kwargs: {
            "identity": {
                "query": "x",
                "cas_no": "",
                "smiles": None,
                "cid": None,
                "source": "none",
                "resolved": False,
            },
            "external": [],
            "external_meta": {
                "enabled": True,
                "queried": False,
                "count": 0,
                "skipped_reason": "无法解析 SMILES（聚合物/商品名常见）；仅展示库内候选",
                "provider": "pubchem_fastsimilarity_2d",
            },
        },
    )
    response = client.post(
        "/api/materials/substitutes",
        json={
            "requirement": {"domain": "anticorrosion_coating", "voc_limit_gpl": 420},
            "material": "Polyamide hardener",
            "limit": 3,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "external" in body
    assert "external_meta" in body
    assert body["external_meta"]["provider"] == "pubchem_fastsimilarity_2d"


def test_llm_auto_skipped_when_catalog_rich(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_alternatives.fetch_llm_alternatives",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not query llm")),
    )
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=False,
        include_literature=False,
        include_llm=None,
        limit=10,
    )
    assert len(report["candidates"]) >= 3 or report["llm_meta"]["mode"] == "auto"
    if len(report["candidates"]) >= 3:
        assert report["llm"] == []
        assert report["llm_meta"]["mode"] == "auto"
        assert "auto_skipped" in (report["llm_meta"]["skipped_reason"] or "")


def test_llm_forced_merges_rules(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_alternatives.fetch_llm_alternatives",
        lambda **kwargs: {
            "llm": [
                {
                    "name": "Waterborne polyisocyanate (hydrophilic HDI)",
                    "kind": "substitute_crosslinker",
                    "rationale": "rules",
                    "source": "chemist_rules",
                    "cas_no": None,
                    "smiles": None,
                    "role_hint": "hardener",
                    "in_catalog": True,
                    "catalog_name": "Waterborne polyisocyanate (hydrophilic HDI)",
                    "note": "AI/规则建议；未做配方 Δ",
                }
            ],
            "llm_meta": {
                "enabled": True,
                "queried": True,
                "count": 1,
                "skipped_reason": None,
                "providers": ["chemist_rules"],
            },
        },
    )
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=False,
        include_literature=False,
        include_llm=True,
        limit=10,
    )
    assert report["llm_meta"]["mode"] == "forced"
    assert report["llm"][0]["name"].startswith("Waterborne")
    assert "llm" in report["layers_used"]


def test_llm_auto_runs_when_catalog_scarce(monkeypatch):
    from app.domain.genome import FormulationGenome, Slot

    monkeypatch.setattr(
        "app.services.llm_alternatives.fetch_llm_alternatives",
        lambda **kwargs: {
            "llm": [
                {
                    "name": "Lanthanum nitrate",
                    "kind": "substitute_inhibitor",
                    "rationale": "rare earth",
                    "source": "llm_expand",
                    "in_catalog": False,
                    "catalog_name": None,
                    "note": "AI/规则建议；未做配方 Δ",
                }
            ],
            "llm_meta": {
                "enabled": True,
                "queried": True,
                "count": 1,
                "skipped_reason": None,
                "providers": ["llm_expand"],
            },
        },
    )
    # Use a nonsense role so catalog pool is empty → auto L4.
    genome = FormulationGenome(
        domain=ProductDomain.anticorrosion_coating,
        slots=[
            Slot(role="mystery", material="Unknown Widget X", weight_pct=100),
        ],
    )
    report = find_substitutes(
        genome,
        0,
        _req(),
        include_external=False,
        include_literature=False,
        include_llm=None,
        limit=10,
    )
    assert report["candidates"] == []
    assert report["llm_meta"]["mode"] == "auto"
    assert report["llm"][0]["name"] == "Lanthanum nitrate"
    assert "llm" in report["layers_used"]


def test_include_llm_false_skips():
    genome = _genome()
    report = find_substitutes(
        genome,
        _slot_of(genome, "hardener"),
        _req(),
        include_external=False,
        include_literature=False,
        include_llm=False,
    )
    assert report["llm"] == []
    assert report["llm_meta"]["mode"] == "off"
    assert report["llm_meta"]["skipped_reason"] == "include_llm=false"