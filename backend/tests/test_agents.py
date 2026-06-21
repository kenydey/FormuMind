"""Tests for the v0.8 hierarchical multi-agent review layer.

Covers the canonical Chemist Agent interception (water-insoluble blocked
isocyanate in a waterborne system), the solvent-borne non-interception case,
free-isocyanate detection, the Inspector Agent (REACH SVHC + VOC), the supervisor
aggregation, the pure-JSON endpoint contract, and the no-op Redis bus.
All run fully offline (no LLM, no Redis).
"""
from fastapi.testclient import TestClient

from app.agents import bus
from app.agents.chemist import ChemistAgent
from app.agents.inspector import InspectorAgent
from app.agents.supervisor import InitializeAgent
from app.domain.knowledge import ingredient
from app.domain.schemas import (
    Formulation,
    ProductDomain,
    Requirement,
)
from app.main import app

client = TestClient(app)


def _form(name: str, domain: ProductDomain, items: list[tuple[str, float]], **kw) -> Formulation:
    return Formulation(
        name=name,
        domain=domain,
        ingredients=[ingredient(n, pct) for n, pct in items],
        **kw,
    )


def _waterborne_with_desmodur() -> Formulation:
    return _form(
        "Waterborne PU primer (with Desmodur BL 3175)",
        ProductDomain.anticorrosion_coating,
        [
            ("Deionized water", 40.0),
            ("Waterborne acrylic emulsion", 30.0),
            ("Desmodur BL 3175", 15.0),
            ("Titanium dioxide", 15.0),
        ],
    )


# ── Chemist Agent: canonical interception ────────────────────────────────────

def test_desmodur_in_waterborne_is_intercepted():
    finding = ChemistAgent().inspect(_waterborne_with_desmodur(), explain=False)
    assert finding.agent == "chemist"
    assert finding.status == "intercept"
    assert finding.engine == "deterministic"

    iso = [i for i in finding.issues if i.ingredient == "Desmodur BL 3175"]
    assert iso, "Desmodur should raise an issue"
    issue = iso[0]
    assert issue.code == "isocyanate_water_incompatibility"
    assert issue.severity == "high"

    # Must proactively recommend waterborne alternatives + a waterborne catalyst.
    suggestions = {r.suggestion for r in issue.recommendations}
    assert "Waterborne polyisocyanate (hydrophilic HDI)" in suggestions
    assert "Bismuth neodecanoate" in suggestions
    kinds = {r.kind for r in issue.recommendations}
    assert "substitute_crosslinker" in kinds
    assert "swap_catalyst" in kinds


def test_desmodur_in_solventborne_is_not_intercepted():
    form = _form(
        "Solventborne PU primer (with Desmodur BL 3175)",
        ProductDomain.anticorrosion_coating,
        [
            ("Xylene", 40.0),
            ("Bisphenol-A epoxy (DGEBA)", 30.0),
            ("Desmodur BL 3175", 15.0),
            ("Titanium dioxide", 15.0),
        ],
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert finding.status in ("pass", "warn")
    assert not any(
        i.code in ("isocyanate_water_incompatibility", "free_isocyanate_in_water")
        for i in finding.issues
    )


def test_free_isocyanate_in_waterborne_is_intercepted():
    # "Blocked isocyanate (IPDI)" carries a free-NCO SMILES → RDKit SMARTS hit
    # when RDKit is installed; the carrier rule catches it otherwise. Either way
    # it must be a high-severity interception.
    form = _form(
        "Waterborne primer (with free-NCO IPDI)",
        ProductDomain.anticorrosion_coating,
        [
            ("Deionized water", 45.0),
            ("Waterborne acrylic emulsion", 30.0),
            ("Blocked isocyanate (IPDI)", 10.0),
            ("Titanium dioxide", 15.0),
        ],
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert finding.status == "intercept"
    codes = {i.code for i in finding.issues}
    assert codes & {"free_isocyanate_in_water", "isocyanate_water_incompatibility"}


def test_acid_base_conflict_is_warned():
    form = _form(
        "Acid + base mix",
        ProductDomain.surface_treatment,
        [
            ("Phosphoric acid", 8.0),
            ("Sodium hydroxide", 6.0),
            ("Deionized water", 86.0),
        ],
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert finding.status == "warn"
    assert any(i.code == "acid_base_conflict" for i in finding.issues)


def test_clean_waterborne_formulation_passes_chemist():
    form = _form(
        "Clean waterborne acrylic",
        ProductDomain.anticorrosion_coating,
        [
            ("Deionized water", 45.0),
            ("Waterborne acrylic emulsion", 35.0),
            ("Titanium dioxide", 20.0),
        ],
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert finding.status == "pass"
    assert finding.issues == []


# ── Inspector Agent: regulatory ──────────────────────────────────────────────

def test_inspector_flags_svhc():
    form = _form(
        "Primer with SVHC inhibitor",
        ProductDomain.anticorrosion_coating,
        [
            ("Bisphenol-A epoxy (DGEBA)", 50.0),
            ("Zinc molybdate", 10.0),
            ("Xylene", 40.0),
        ],
    )
    finding = InspectorAgent().inspect(form)
    assert finding.status == "warn"
    svhc = [i for i in finding.issues if i.code == "svhc"]
    assert svhc and svhc[0].ingredient == "Zinc molybdate"


def test_inspector_flags_voc_exceedance():
    form = _form(
        "High-VOC primer",
        ProductDomain.anticorrosion_coating,
        [("Bisphenol-A epoxy (DGEBA)", 60.0), ("Xylene", 40.0)],
    )
    form.predicted["voc_gpl"] = 500.0
    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=250.0)
    finding = InspectorAgent().inspect(form, requirement=req)
    assert any(i.code == "voc_exceedance" for i in finding.issues)


def test_inspector_passes_clean_formulation():
    form = _form(
        "Clean primer",
        ProductDomain.anticorrosion_coating,
        [("Waterborne acrylic emulsion", 60.0), ("Deionized water", 40.0)],
    )
    finding = InspectorAgent().inspect(form)
    assert finding.status == "pass"


# ── Supervisor aggregation ───────────────────────────────────────────────────

def test_supervisor_aggregates_worst_status_and_merges_recs():
    form = _waterborne_with_desmodur()
    form.ingredients.append(ingredient("Zinc molybdate", 5.0))  # add an SVHC too
    verdict = InitializeAgent().review(form, explain=False)

    assert verdict.overall_status == "intercept"  # chemist intercept dominates
    agents = {f.agent for f in verdict.findings}
    assert agents == {"chemist", "inspector"}
    assert verdict.engine == "deterministic"
    # Recommendations are merged across agents and de-duplicated.
    assert verdict.recommendations
    keys = [(r.kind, r.target, r.suggestion) for r in verdict.recommendations]
    assert len(keys) == len(set(keys))


def test_supervisor_pass_on_clean_formulation():
    form = _form(
        "Clean waterborne",
        ProductDomain.anticorrosion_coating,
        [("Waterborne acrylic emulsion", 60.0), ("Deionized water", 40.0)],
    )
    verdict = InitializeAgent().review(form, explain=False)
    assert verdict.overall_status == "pass"


# ── API endpoint: pure-JSON contract ─────────────────────────────────────────

def test_review_endpoint_returns_pure_json_intercept():
    form = _waterborne_with_desmodur()
    payload = {"formulation": form.model_dump(), "explain": False}
    r = client.post("/api/agents/review", json=payload)
    assert r.status_code == 200
    data = r.json()  # must parse as pure JSON (no markdown wrapping)
    assert data["overall_status"] == "intercept"
    assert data["formulation_name"] == form.name
    assert any(f["agent"] == "chemist" for f in data["findings"])
    assert isinstance(data["recommendations"], list)


# ── Reserved Redis Pub/Sub bus: no-op when disabled ──────────────────────────

def test_bus_publish_is_noop_when_disabled():
    assert bus.publish("agent_events", {"event": "test"}) is False
    assert bus.subscribe("agent_events") is None


def test_bus_unknown_channel_is_false():
    assert bus.publish("does_not_exist", {"x": 1}) is False
