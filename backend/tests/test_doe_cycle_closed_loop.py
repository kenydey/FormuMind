"""Wave 2: DOE cycle closed-loop + service split regression tests.

Covers:
1. ``load_prior_measurements`` reads the training registry (project-scoped).
2. ``generate_experiment_dicts`` passes prior measurements to BayBE
   (the pre-fix bug: ``measurements=[]`` hardcoded, round 2 never learned
   from round 1).
3. ``run_doe_cycle`` still exposes the same signature / result contract.
4. End-to-end: two full cycles with a mocked engine; the second cycle's
   BayBE call must receive the first cycle's measured results.
"""
from __future__ import annotations

from types import SimpleNamespace


def _requirement():
    from app.domain.schemas import ProductDomain

    return SimpleNamespace(
        domain=ProductDomain.anticorrosion_coating,
        project_id="proj-wave2",
    )


def _record():
    from app.domain.schemas import ExperimentRecord, ProductDomain

    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id="proj-wave2",
        factors={"resin_wt_pct": 62.0},
        measured={"corrosion_rate": 0.42},
        label="round-1-exp",
    )


def _mock_db(monkeypatch, mod):
    """Fake ExperimentRow/session/commit_session (no real DB writes)."""

    class _ExpRow:
        _counter = 0

        def __init__(self, **kwargs):
            type(self)._counter += 1
            self.id = f"exp-{type(self)._counter}"

    class _Session:
        def add(self, obj):
            pass

        def flush(self):
            pass

    class _CM:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(mod, "ExperimentRow", _ExpRow)
    monkeypatch.setattr(mod, "default_session_factory", lambda: object())
    monkeypatch.setattr(mod, "commit_session", lambda factory: _CM())


def _mock_recommendations(monkeypatch, mod, n=2):
    import app.api.formulations as form_mod

    cands = [SimpleNamespace(name=f"cand-{i}") for i in range(n)]

    class _Req:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(form_mod, "RecommendFormulationsRequest", _Req)
    monkeypatch.setattr(
        form_mod, "recommend_formulations", lambda req: SimpleNamespace(formulations=cands)
    )


class _FakeBaybe:
    """Captures recommend() kwargs; returns a deterministic fake plan."""

    instances: list = []

    def __init__(self):
        type(self).instances.append(self)
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def recommend(self, req, **kwargs):
        self.calls.append(kwargs)
        run = SimpleNamespace(
            run_id=f"baybe-{len(self.calls)}",
            coded={"x1": 0.1},
            natural={"resin_wt_pct": 63.0},
            ai_suggested=True,
            infeasible=False,
            infeasible_reason=None,
        )
        return SimpleNamespace(plan=SimpleNamespace(runs=[run]))


def _mock_baybe(monkeypatch):
    import app.services.engines.baybe_engine as baybe_mod

    _FakeBaybe.instances.clear()
    monkeypatch.setattr(baybe_mod, "BaybeCampaignEngine", _FakeBaybe)
    return _FakeBaybe


def test_load_prior_measurements_reads_registry(monkeypatch):
    from app.services import doe_cycle_service as mod
    from app.services.training import registry

    # isolate: clear registry records for this domain/project first
    before = mod.load_prior_measurements(_requirement())
    n_before = len(before)

    registry.add([_record()], retrain=False)
    try:
        after = mod.load_prior_measurements(_requirement())
        assert len(after) == n_before + 1
        assert after[-1].label == "round-1-exp"
        # project scoping: other project must not leak in
        other = SimpleNamespace(
            domain=_requirement().domain, project_id="other-proj"
        )
        assert all(r.label != "round-1-exp" for r in mod.load_prior_measurements(other))
    finally:
        # best-effort cleanup: drop the record we added
        with registry._lock:
            registry._records[:] = [
                r for r in registry._records if r.label != "round-1-exp"
            ]


def test_load_prior_measurements_fail_open(monkeypatch):
    from app.services import doe_cycle_service as mod
    import app.services.training as training_mod

    class _Boom:
        def records_for(self, *a, **k):
            raise RuntimeError("registry down")

    monkeypatch.setattr(training_mod, "registry", _Boom())
    assert mod.load_prior_measurements(_requirement()) == []


def test_generate_passes_measurements_to_baybe(monkeypatch):
    from app.services import doe_cycle_service as mod

    fake_cls = _mock_baybe(monkeypatch)
    rec = _record()
    engine, dicts = mod.generate_experiment_dicts(_requirement(), [rec])
    assert engine == "baybe"
    assert len(dicts) == 1
    assert dicts[0]["natural_factors"] == {"resin_wt_pct": 63.0}
    inst = fake_cls.instances[-1]
    assert len(inst.calls) == 1
    # THE closed-loop assertion: BayBE must receive prior measurements,
    # not an empty list.
    assert inst.calls[0]["measurements"] == [rec]


def test_run_doe_cycle_contract_unchanged(monkeypatch):
    from app.services import doe_cycle_service as mod

    _mock_db(monkeypatch, mod)
    _mock_recommendations(monkeypatch, mod)
    _mock_baybe(monkeypatch)
    # empty registry -> cold start still works
    result = mod.run_doe_cycle(_requirement())
    assert result["status"] == "success"
    assert result["count"] == 1
    assert len(result["experiment_ids"]) == 1


def _e2e_requirement():
    import uuid

    from app.domain.schemas import ProductDomain

    return SimpleNamespace(
        domain=ProductDomain.anticorrosion_coating,
        project_id=f"proj-wave2-e2e-{uuid.uuid4().hex[:8]}",
    )


def _e2e_record(project_id):
    from app.domain.schemas import ExperimentRecord, ProductDomain

    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id=project_id,
        factors={"resin_wt_pct": 62.0},
        measured={"corrosion_rate": 0.42},
        label="round-1-exp",
    )


def test_two_cycles_second_uses_first_results(monkeypatch):
    """End-to-end: cycle 2's BayBE call must see cycle 1's measurements."""
    from app.services import doe_cycle_service as mod
    from app.services.training import registry

    req = _e2e_requirement()
    _mock_db(monkeypatch, mod)
    _mock_recommendations(monkeypatch, mod)
    fake_cls = _mock_baybe(monkeypatch)

    # cycle 1: cold start for THIS project — no priors carrying our label
    assert not [r for r in mod.load_prior_measurements(req) if r.label == "round-1-exp"]
    r1 = mod.run_doe_cycle(req)
    assert r1["status"] == "success"
    got1 = [r for r in fake_cls.instances[-1].calls[0]["measurements"] if r.label == "round-1-exp"]
    assert got1 == []

    # simulate measurement ingestion (the workbench-sync / CSV-import path)
    registry.add([_e2e_record(req.project_id)], retrain=False)
    try:
        # cycle 2: real load_prior_measurements must now return round-1 data,
        # and BayBE must receive it.
        priors = [
            r for r in mod.load_prior_measurements(req)
            if r.label == "round-1-exp" and r.project_id == req.project_id
        ]
        assert len(priors) == 1

        r2 = mod.run_doe_cycle(req)
        assert r2["status"] == "success"
        got = [
            r for r in fake_cls.instances[-1].calls[0]["measurements"]
            if r.label == "round-1-exp" and r.project_id == req.project_id
        ]
        assert len(got) == 1
        assert got[0].factors == {"resin_wt_pct": 62.0}
    finally:
        with registry._lock:
            registry._records[:] = [
                r for r in registry._records if r.label != "round-1-exp"
            ]
