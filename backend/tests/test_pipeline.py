from app.domain.schemas import ProductDomain, Requirement, Substrate
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


def test_build_doe_includes_cure_factor_for_coatings():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=100)
    plan = workflow.build_doe(req, design="full_factorial")
    names = [f.name for f in plan.factors]
    assert "cure_temperature_c" in names
    assert len(plan.runs) == 2 ** len(plan.factors)


def test_optimization_improves_objective_over_baseline():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=600)
    result = workflow.run_optimization(req, iterations=20)
    assert result.objective == "salt_spray_hours"
    assert len(result.top_formulations) == 5
    # Best-so-far convergence curve is monotonically non-decreasing.
    assert all(b >= a for a, b in zip(result.history, result.history[1:]))
    # Optimized top beats the neutral baseline formulation.
    from app.domain import knowledge
    from app.services import predictor

    baseline = knowledge.baseline_formulation(req)
    base_score = predictor.objective_value(baseline, result.objective)
    assert result.top_formulations[0].score >= base_score


def test_all_domains_run_end_to_end():
    for domain in ProductDomain:
        req = Requirement(domain=domain, substrate=Substrate.aluminum)
        research = workflow.run_research(req)
        assert research.recommended
        opt = workflow.run_optimization(req, iterations=10)
        assert opt.top_formulations
