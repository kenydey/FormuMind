from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement, Substrate
from app.pipeline import workflow


def test_research_returns_evidence_and_recommendations():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500, film_weight_gsm=70)
    result = workflow.run_research(req)
    assert result.evidence, "should retrieve prior art"
    assert len(result.recommended) == 3
    # Recommendations are scored and sorted descending.
    scores = [f.score for f in result.recommended]
    assert scores == sorted(scores, reverse=True)
    assert "salt_spray_hours" in result.recommended[0].predicted
    assert result.chat_markdown


def test_research_populates_cost_and_sustainability():
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    result = workflow.run_research(req)
    for form in result.recommended:
        assert "cost_cny_per_kg" in form.predicted
        assert "voc_gpl" in form.predicted
        assert "sustainability_idx" in form.predicted
        assert form.predicted["cost_cny_per_kg"] > 0


def test_research_voc_warning_when_over_limit():
    # Set an extremely low VOC limit so the solvent-based coating triggers a warning.
    req = Requirement(domain=ProductDomain.anticorrosion_coating, voc_limit_gpl=1.0)
    result = workflow.run_research(req)
    # At least one recommended formulation should warn about VOC.
    warning_found = any(
        any("VOC" in w for w in f.warnings)
        for f in result.recommended
    )
    assert warning_found


def test_research_source_types_filters_preloaded():
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    from app.domain.schemas import Evidence

    mixed = [
        Evidence(source="USPTO", identifier="US1", title="Pat", snippet="p", relevance=0.9),
        Evidence(source="literature", identifier="DOI:1", title="Paper", snippet="l", relevance=0.8),
    ]
    result = workflow.run_research(req, pre_sources=mixed, source_types=["literature"])
    assert result.evidence
    assert all("literature" in e.source.lower() or e.identifier.startswith("DOI") for e in result.evidence)


def test_research_source_types_live_search():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    result = workflow.run_research(req, source_types=["patents", "literature"])
    assert result.evidence
    assert len(result.recommended) == 3


def test_build_doe_includes_cure_factor_for_coatings():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=100)
    plan = workflow.build_doe(req, design="full_factorial")
    names = [f.name for f in plan.factors]
    assert "cure_temperature_c" in names
    assert len(plan.runs) == 2 ** len(plan.factors)


def test_optimization_single_objective_improves_over_baseline():
    # Explicit single-objective: purely maximise salt spray.
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=600,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
    )
    result = workflow.run_optimization(req, iterations=20)
    assert result.objective == "salt_spray_hours"
    assert len(result.top_formulations) == 5
    # Best-so-far convergence curve is monotonically non-decreasing.
    assert all(b >= a for a, b in zip(result.history, result.history[1:]))
    # Optimized top beats the neutral baseline on the salt-spray metric.
    from app.domain import knowledge
    from app.services import predictor

    baseline = knowledge.baseline_formulation(req)
    base_score = predictor.objective_value(baseline, result.objective)
    assert result.top_formulations[0].score >= base_score


def test_optimization_multi_objective_returns_objectives():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    result = workflow.run_optimization(req, iterations=10)
    # Default multi-objective: objectives list should be populated.
    assert result.objectives, "multi-objective result should carry objective specs"
    assert any(o.metric == "salt_spray_hours" for o in result.objectives)
    assert any(o.metric == "cost_cny_per_kg" for o in result.objectives)
    # predicted_std should be populated (may be empty if no trained models).
    for form in result.top_formulations:
        assert isinstance(form.predicted_std, dict)


def test_all_domains_run_end_to_end():
    for domain in ProductDomain:
        req = Requirement(domain=domain, substrate=Substrate.aluminum)
        research = workflow.run_research(req)
        assert research.recommended
        opt = workflow.run_optimization(req, iterations=10)
        assert opt.top_formulations
