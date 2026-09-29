"""P1-1 golden: formulation recommendation output quality gate.

Runs without an LLM and without the KB: grounding quality is asserted
directly against ``ground_recommended_formulas`` with fixed synthetic
inputs, plus one API wiring test with mocked retrieval.

Thresholds were calibrated on main 2026-09-29: clean synthetic set scores
high_ratio=0.938 (90/96 components high); a set with one hallucinated
component per formula scores 0.75. The 0.85 gate separates them.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.knowledge import RAW_MATERIALS
from app.domain.schemas import (
    Evidence,
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaComponent,
)
from app.main import app
from app.services.grounded_recommend import ground_recommended_formulas

# Golden gate: fraction of components tagged high-confidence on the clean set.
HIGH_CONFIDENCE_RATIO_GATE = 0.85

DOMAINS = [
    ProductDomain.anticorrosion_coating,
    ProductDomain.degreaser,
    ProductDomain.surface_treatment,
]
CATALOG_NAMES = list(RAW_MATERIALS.keys())

# Golden-local "never" lists: ingredients that must never appear in a
# recommendation for the domain. Test-local only, not product logic.
NEVER_LIST: dict[ProductDomain, list[str]] = {
    ProductDomain.anticorrosion_coating: ["sucrose", "aspartame", "penicillin"],
    ProductDomain.degreaser: ["sucrose", "aspartame", "penicillin"],
    ProductDomain.surface_treatment: ["sucrose", "aspartame", "penicillin"],
}

# A component present in neither the catalog nor the evidence: must be
# flagged low-confidence with no evidence refs (mutation probe).
HALLUCINATED = RecommendedFormulaComponent(
    name="Unobtainium X-999",
    zh_name="",
    cas_no="000-00-0",
    weight_pct=5.0,
)


def _clean_formula(domain: ProductDomain, idx: int) -> RecommendedFormula:
    comps = []
    picks = [CATALOG_NAMES[(idx * 4 + j) % len(CATALOG_NAMES)] for j in range(4)]
    for name, pct in zip(picks, [40.0, 30.0, 20.0, 10.0]):
        spec = RAW_MATERIALS[name]
        comps.append(
            RecommendedFormulaComponent(
                name=name,
                zh_name=str(spec.get("zh_name") or ""),
                cas_no=str(spec.get("cas_no") or ""),
                weight_pct=pct,
            )
        )
    return RecommendedFormula(
        name=f"Golden-{domain.value}-{idx}",
        domain=domain,
        rationale="P1-1 golden clean input",
        components=comps,
    )


def _evidence() -> list[Evidence]:
    evs = []
    for i in range(0, len(CATALOG_NAMES), 8):
        chunk = CATALOG_NAMES[i : i + 8]
        evs.append(
            Evidence(
                source="seed",
                identifier=f"p11-{i}",
                title=f"P1-1 golden doc {i}",
                snippet="Formulation uses " + ", ".join(chunk) + ".",
                relevance=0.9,
            )
        )
    return evs


def _high_ratio(formulas: list[RecommendedFormula]) -> float:
    total = sum(len(f.components) for f in formulas)
    high = sum(
        1 for f in formulas for c in f.components if c.grounding_confidence == "high"
    )
    assert total > 0
    return high / total


def test_golden_grounding_high_confidence_ratio():
    """Clean synthetic formulas: high-confidence ratio clears the gate."""
    formulas = [_clean_formula(d, i) for d in DOMAINS for i in range(8)]
    grounded, _ = ground_recommended_formulas(formulas, _evidence())
    assert _high_ratio(grounded) >= HIGH_CONFIDENCE_RATIO_GATE


def test_golden_grounding_preserves_weight_pct_sums():
    """Grounding must not corrupt numeric payloads: weight_pct still sums ~100."""
    formulas = [_clean_formula(d, i) for d in DOMAINS for i in range(8)]
    grounded, _ = ground_recommended_formulas(formulas, _evidence())
    for f in grounded:
        total = sum(c.weight_pct or 0.0 for c in f.components)
        assert abs(total - 100.0) < 1e-6, f.name


def test_golden_grounding_flags_hallucinated_component():
    """Mutation probe: an invented component must be flagged low, refs empty."""
    formulas = [_clean_formula(d, i) for d in DOMAINS for i in range(8)]
    for f in formulas:
        f.components.append(HALLUCINATED.model_copy(deep=True))
    grounded, _ = ground_recommended_formulas(formulas, _evidence())
    flagged = [
        c
        for f in grounded
        for c in f.components
        if c.name == "Unobtainium X-999"
    ]
    assert len(flagged) == len(formulas)
    for c in flagged:
        assert c.grounding_confidence == "low"
        assert c.evidence_refs == []
    # And the gate itself must catch the mutated set (discriminative power).
    assert _high_ratio(grounded) < HIGH_CONFIDENCE_RATIO_GATE


def test_golden_never_list_absent():
    """Golden-local denylist: no forbidden ingredient may appear."""
    formulas = [_clean_formula(d, i) for d in DOMAINS for i in range(8)]
    grounded, _ = ground_recommended_formulas(formulas, _evidence())
    for f in grounded:
        forbidden = NEVER_LIST[f.domain]
        for c in f.components:
            hay = f"{c.name} {c.zh_name}".lower()
            assert not any(bad in hay for bad in forbidden), (f.name, c.name)


def test_golden_recommend_api_wiring(monkeypatch):
    """API wiring: hybrid mode, mocked retrieval, offline LLM fallback.

    The offline fallback produces deterministic formulas from the materials
    catalog; grounding must tag their components (not silently drop them).
    """
    from app.pipeline import research_graph as rg_mod

    ev = Evidence(
        source="seed",
        identifier="p11-api",
        title="API wiring doc",
        snippet=(
            "Baseline anticorrosion formulation uses Bisphenol-A epoxy (DGEBA), "
            "Polyamide hardener, Zinc phosphate, Isophorone diamine (IPDA)."
        ),
        relevance=0.9,
    )
    fake = rg_mod.GroundedEvidenceResult(
        query="q",
        evidence=[ev],
        grounded_evidence=[ev],
        grade="correct",
        grade_reason="mock",
    )
    monkeypatch.setattr(
        rg_mod, "resolve_grounded_evidence", lambda *a, **k: fake
    )
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        client = TestClient(app)
        from app.domain.schemas import Requirement

        req = Requirement(domain=ProductDomain.anticorrosion_coating)
        res = client.post(
            "/api/formulations/recommend",
            json={"requirement": req.model_dump(), "n": 2},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["engine"] == "offline"
        assert len(body["formulas"]) >= 1
        # Grounding ran on the fallback formulas: components carry confidence.
        comps = [
            c
            for f in body["formulas"]
            for c in f.get("components", [])
        ]
        assert comps, "offline formulas should carry components"
        assert all(
            c.get("grounding_confidence") in ("high", "medium", "low")
            for c in comps
        )
    finally:
        get_settings.cache_clear()
