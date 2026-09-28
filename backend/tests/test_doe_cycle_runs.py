"""Wave 3-2: DOE closed-loop cycle run observability.

Covers: DOECycleRunRow recording (fail-open), engine/prior-count on the
cycle result contract, and GET /api/doe/cycle-runs (fail-closed
project_id, items + summary shape).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, DOECycleRunRow


@pytest.fixture()
def mem_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _factory():
        return factory()

    return _factory


def test_record_cycle_run_persists_row(mem_session_factory, monkeypatch):
    from app.services import doe_cycle_service as mod

    monkeypatch.setattr(mod, "default_session_factory", lambda: mem_session_factory)
    mod.record_cycle_run(
        project_id="p1",
        domain="anticorrosion_coating",
        engine="baybe",
        prior_measurement_count=3,
        experiment_count=5,
        status="success",
    )
    with mem_session_factory() as s:
        rows = s.query(DOECycleRunRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.project_id, r.domain, r.engine) == ("p1", "anticorrosion_coating", "baybe")
    assert (r.prior_measurement_count, r.experiment_count) == (3, 5)
    assert r.status == "success"
    assert r.created_at is not None


def test_record_cycle_run_fail_open(monkeypatch):
    """A recording failure must never break the cycle."""
    from app.services import doe_cycle_service as mod

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "default_session_factory", boom)
    mod.record_cycle_run(
        project_id="p1", domain="d", engine="lhs",
        prior_measurement_count=0, experiment_count=0, status="error",
    )  # must not raise


def test_run_doe_cycle_result_enriched(monkeypatch):
    """engine + prior_measurement_count ride on the task result."""
    from app.services import doe_cycle_service as mod
    from app.domain.schemas import ProductDomain

    # minimal stubs: cold start, one candidate, fake baybe
    class _CM:
        def __enter__(self):
            return SimpleNamespace(add=lambda *a: None, flush=lambda: None)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod, "default_session_factory", lambda: object())
    monkeypatch.setattr(mod, "commit_session", lambda factory: _CM())
    monkeypatch.setattr(mod, "ExperimentRow",
                        lambda **kw: SimpleNamespace(id=1, **kw))
    monkeypatch.setattr(mod, "load_prior_measurements", lambda req: [])
    monkeypatch.setattr(
        mod, "build_candidate_formulations", lambda req: [SimpleNamespace(name="c")]
    )

    class _FakeBaybe:
        def available(self):
            return True

        def recommend(self, req, **kwargs):
            run = SimpleNamespace(
                run_id="b1", coded={}, natural={"x": 1.0},
                ai_suggested=True, infeasible=False, infeasible_reason=None,
            )
            return SimpleNamespace(plan=SimpleNamespace(runs=[run]))

    import app.services.engines.baybe_engine as baybe_mod
    monkeypatch.setattr(baybe_mod, "BaybeCampaignEngine", _FakeBaybe)
    # silence provenance linking
    import app.services.provenance as prov_mod
    monkeypatch.setattr(prov_mod, "formulation_id_for", lambda f: "f1")
    monkeypatch.setattr(prov_mod, "link", lambda *a: None)

    req = SimpleNamespace(domain=ProductDomain.anticorrosion_coating,
                          project_id="p9")
    result = mod.run_doe_cycle(req)
    assert result["status"] == "success"
    assert result["engine"] == "baybe"
    assert result["prior_measurement_count"] == 0


def _seed(factory, project_id, n=2):
    with factory() as s:
        for i in range(n):
            s.add(
                DOECycleRunRow(
                    project_id=project_id,
                    domain="anticorrosion_coating",
                    engine="lhs" if i % 2 else "baybe",
                    prior_measurement_count=i,
                    experiment_count=5,
                    status="success",
                )
            )
        s.commit()


def test_cycle_runs_endpoint_shape(mem_session_factory, monkeypatch):
    from app.api import doe as doe_api

    _seed(mem_session_factory, "proj-1", n=2)
    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: mem_session_factory
    )
    stub_registry = SimpleNamespace(records_for=lambda d, project_id="": ["m"] * 7)
    monkeypatch.setattr("app.services.training.registry", stub_registry)

    resp = doe_api.doe_cycle_runs(project_id="proj-1", limit=20)
    assert len(resp.items) == 2
    # newest first
    assert resp.items[0].id > resp.items[1].id
    assert resp.items[0].engine in ("baybe", "lhs")
    assert resp.summary["cycle_count"] == 2
    assert resp.summary["total_experiments"] == 10
    assert resp.summary["measured_count"] == 7 * 4  # 4 ProductDomain values
    assert resp.summary["last_engine"] == resp.items[0].engine
    assert resp.summary["last_status"] == "success"


def test_cycle_runs_fail_closed_on_empty_project():
    from app.api import doe as doe_api

    with pytest.raises(HTTPException) as ei:
        doe_api.doe_cycle_runs(project_id="  ", limit=20)
    assert ei.value.status_code == 422


def test_cycle_runs_empty_project_returns_zeros(mem_session_factory, monkeypatch):
    from app.api import doe as doe_api

    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: mem_session_factory
    )
    stub_registry = SimpleNamespace(records_for=lambda d, project_id="": [])
    monkeypatch.setattr("app.services.training.registry", stub_registry)

    resp = doe_api.doe_cycle_runs(project_id="no-such", limit=20)
    assert resp.items == []
    assert resp.summary["cycle_count"] == 0
    assert resp.summary["last_engine"] == ""
