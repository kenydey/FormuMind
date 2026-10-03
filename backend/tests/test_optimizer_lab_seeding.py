"""The fallback optimizer learns from measured lab experiments.

``run_optimization`` accepted ``existing_records`` but, off the BayBE path, used
them only to pick the ``measurement_source`` label — and the label itself was
computed from the *argument*, so the common call with ``existing_records=None``
(``/api/optimize``, the Celery task) always reported ``predictor_virtual`` even
when a campaign had real measurements in the registry. The numpy / Optuna /
BoTorch optimizers only ever saw predictor scores; lab data reached them solely
through the retrained surrogate, which needs ``min_train_samples`` per metric.
"""
from __future__ import annotations

import pytest

from app.domain import knowledge
from app.domain.project_spec import normalize_requirement, resolve_levers
from app.domain.schemas import ExperimentRecord, ObjectiveSpec, ProductDomain, Requirement
from app.pipeline import reconstruct, workflow
from app.services import predictor
from app.services.optimizer import BayesianOptimizer, Factor


def _req(**kw) -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=120,
        salt_spray_hours=500,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
        **kw,
    )


def _setup(req: Requirement):
    norm = normalize_requirement(req)
    base = norm.active_formulation or knowledge.baseline_formulation(norm)
    levers = resolve_levers(norm, base)
    factors = [Factor(name=l.name, low=l.low, high=l.high) for l in levers]
    process = workflow.process_for(norm)
    return norm, base, levers, factors, process


def _rec(factors: dict, measured: dict, **kw) -> ExperimentRecord:
    return ExperimentRecord(
        domain=kw.pop("domain", ProductDomain.anticorrosion_coating),
        factors=factors,
        measured=measured,
        **kw,
    )


# ── _lab_points ─────────────────────────────────────────────────────────────


def test_lab_point_uses_recorded_levers_and_baseline_for_the_rest():
    norm, base, levers, factors, process = _setup(_req())
    lever = factors[0]
    mid = (lever.low + lever.high) / 2
    pts = workflow._lab_points(
        [_rec({lever.name: mid}, {"salt_spray_hours": 900.0})], norm, factors, base, levers, process
    )
    assert len(pts) == 1
    x, measured = pts[0]
    baseline = reconstruct.baseline_lever_values(levers, base, process)
    assert x[0] == pytest.approx(mid)
    assert x[1:] == pytest.approx([baseline[f.name] for f in factors[1:]])
    assert measured == {"salt_spray_hours": 900.0}


def test_recorded_values_are_clipped_into_the_lever_range():
    norm, base, levers, factors, process = _setup(_req())
    lever = factors[0]
    (x, _), = workflow._lab_points(
        [_rec({lever.name: lever.high * 10}, {"salt_spray_hours": 1.0})],
        norm, factors, base, levers, process,
    )
    assert x[0] == lever.high


def test_cure_temperature_field_counts_as_the_cure_lever():
    norm, base, levers, factors, process = _setup(_req())
    names = [f.name for f in factors]
    if "cure_temperature_c" not in names:
        pytest.skip("this domain has no cure_temperature_c lever")
    i = names.index("cure_temperature_c")
    (x, _), = workflow._lab_points(
        [_rec({}, {"salt_spray_hours": 700.0}, cure_temperature_c=factors[i].low)],
        norm, factors, base, levers, process,
    )
    assert x[i] == factors[i].low


def test_irrelevant_records_are_ignored():
    norm, base, levers, factors, process = _setup(_req(project_id="p1"))
    lever = factors[0].name
    good = {"salt_spray_hours": 800.0}
    records = [
        _rec({lever: 20.0}, good, source="virtual"),                               # not measured in a lab
        _rec({lever: 20.0}, {}, ),                                                  # nothing measured
        _rec({lever: 20.0}, good, domain=ProductDomain.degreaser),                 # other domain
        _rec({lever: 20.0}, good, project_id="someone-else"),                      # other project
        _rec({"unrelated lever": 5.0}, good),                                      # none of our levers
        _rec({lever: "abc"}, good),                                                # non-numeric value
        _rec({lever: float("nan")}, good),
        _rec({lever: 20.0}, {"salt_spray_hours": float("nan")}),                   # nothing finite measured
    ]
    assert workflow._lab_points(records, norm, factors, base, levers, process) == []

    kept = workflow._lab_points(
        [_rec({lever: 20.0}, good, project_id="p1"), _rec({lever: 21.0}, good, project_id="")],
        norm, factors, base, levers, process,
    )
    assert len(kept) == 2


def test_only_the_newest_observations_are_kept():
    norm, base, levers, factors, process = _setup(_req())
    lever = factors[0]
    recs = [
        _rec({lever.name: lever.low + (lever.high - lever.low) * i / 400}, {"salt_spray_hours": float(i)})
        for i in range(1, 301)
    ]
    pts = workflow._lab_points(recs, norm, factors, base, levers, process)
    assert len(pts) == workflow._MAX_LAB_OBSERVATIONS
    assert pts[-1][1] == {"salt_spray_hours": 300.0}
    assert pts[0][1] == {"salt_spray_hours": 101.0}


# ── run_optimization ────────────────────────────────────────────────────────


class _Recording(BayesianOptimizer):
    events: list = []

    def suggest(self, n_candidates: int = 64, kappa: float = 1.5):
        x = super().suggest(n_candidates, kappa)
        type(self).events.append(("suggest", list(x)))
        return x

    def observe(self, x, y):
        type(self).events.append(("observe", list(x), float(y)))
        super().observe(x, y)


@pytest.fixture()
def recording(monkeypatch):
    _Recording.events = []
    monkeypatch.setattr(
        workflow, "build_optimizer", lambda factors, seed=0: _Recording(factors=factors, seed=seed)
    )
    return _Recording


def test_lab_measurements_are_observed_after_the_baseline_and_before_any_suggestion(recording):
    req = _req()
    norm, base, levers, factors, process = _setup(req)
    lever = factors[0]
    record = _rec({lever.name: (lever.low + lever.high) / 2}, {"salt_spray_hours": 5000.0})

    result = workflow.run_optimization(req, iterations=2, engine="numpy", existing_records=[record])

    kinds = [e[0] for e in recording.events]
    assert kinds[:2] == ["observe", "observe"], kinds
    assert kinds.count("suggest") == 2 and kinds.index("suggest") >= 2
    baseline_score, lab_score = recording.events[0][2], recording.events[1][2]
    # 5000 h is far above what the model predicts → it scores higher than the baseline
    assert lab_score > baseline_score
    assert recording.events[1][1][0] == pytest.approx((lever.low + lever.high) / 2)
    assert result.lab_points_used == 1
    assert result.measurement_source == "lab"
    # the curve starts from the best known point, and never goes down
    assert result.history[0] >= lab_score
    assert all(b >= a for a, b in zip(result.history, result.history[1:]))


def test_a_better_measurement_scores_higher_than_a_worse_one(recording):
    req = _req()
    norm, base, levers, factors, process = _setup(req)
    name = factors[0].name
    mid = (factors[0].low + factors[0].high) / 2
    lo = _rec({name: mid}, {"salt_spray_hours": 100.0})
    hi = _rec({name: mid}, {"salt_spray_hours": 4000.0})
    workflow.run_optimization(req, iterations=1, engine="numpy", existing_records=[lo, hi])
    scores = [e[2] for e in recording.events if e[0] == "observe"]
    assert scores[1] < scores[2]


def test_no_lab_data_stays_virtual(recording):
    result = workflow.run_optimization(_req(), iterations=1, engine="numpy", existing_records=[])
    assert result.lab_points_used == 0
    assert result.measurement_source == "predictor_virtual"
    assert [e[0] for e in recording.events].count("observe") == 1 + 1  # baseline + the one iteration


def test_records_come_from_the_registry_when_the_caller_passes_none(recording, monkeypatch):
    """The /api/optimize path: existing_records=None must still see lab data."""
    from app.services import training

    req = _req()
    name = _setup(req)[3][0].name
    seen: list = []

    def records_for(domain, project_id=""):
        seen.append((domain, project_id))
        return [_rec({name: _setup(req)[3][0].low}, {"salt_spray_hours": 3000.0})]

    monkeypatch.setattr(training.registry, "records_for", records_for)

    result = workflow.run_optimization(req, iterations=1, engine="numpy")

    assert seen and seen[0][0] == ProductDomain.anticorrosion_coating
    assert result.lab_points_used == 1 and result.measurement_source == "lab"


def test_registry_failure_degrades_to_a_virtual_run(recording, monkeypatch):
    from app.services import training

    def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(training.registry, "records_for", boom)
    result = workflow.run_optimization(_req(), iterations=1, engine="numpy")
    assert result.lab_points_used == 0 and result.measurement_source == "predictor_virtual"


def test_unmeasured_objectives_keep_their_prediction(recording, monkeypatch):
    """A record that measured only cost must not zero out the other objectives."""
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        cure_temperature_c=120,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.5, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.5, direction="minimize"),
        ],
    )
    name = _setup(req)[3][0].name
    seen_props: list[dict] = []
    real = predictor.multi_objective_score

    def spy(form, objectives, process=None, bounds=None, *, props=None):
        seen_props.append(dict(props or {}))
        return real(form, objectives, process, bounds, props=props)

    monkeypatch.setattr(predictor, "multi_objective_score", spy)
    workflow.run_optimization(
        req, iterations=1, engine="numpy",
        existing_records=[_rec({name: 20.0}, {"cost_cny_per_kg": 9.0})],
    )
    lab_call = seen_props[1]  # [0] baseline, [1] the lab observation
    assert lab_call["cost_cny_per_kg"] == 9.0
    assert lab_call.get("salt_spray_hours", 0.0) > 0.0, "predicted value must be kept, not scored as 0"


def test_scoring_reuses_the_prediction_instead_of_predicting_twice(recording, monkeypatch):
    calls = {"predict": 0}
    real_predict = predictor.predict

    def counting(form, process=None):
        calls["predict"] += 1
        return real_predict(form, process)

    monkeypatch.setattr(predictor, "predict", counting)
    seen_props: list = []
    real_score = predictor.multi_objective_score

    def spy(form, objectives, process=None, bounds=None, *, props=None):
        seen_props.append(props)
        return real_score(form, objectives, process, bounds, props=props)

    monkeypatch.setattr(predictor, "multi_objective_score", spy)
    workflow.run_optimization(_req(), iterations=2, engine="numpy", existing_records=[])
    # baseline + 2 iterations are scored with props passed through (no second predict)
    assert all(p is not None for p in seen_props[:3])
