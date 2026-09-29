"""P0-1: DOE plan history project isolation.

- ``doe_plan_store.save`` stamps ``project_id``; ``list_history`` filters by it.
- ``GET /doe/history`` is fail-closed: empty ``project_id`` -> 422.
- Legacy rows (NULL project_id) are excluded from project-scoped reads.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.db import doe_plan_store
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOEFactor, DOERun, ProductDomain


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/doe_iso.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _plan(plan_id: str) -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[DOEFactor(name="x", low=0.0, high=1.0)],
        runs=[DOERun(run_id=1, coded={"x": 0.0}, natural={"x": 0.0})],
        plan_id=plan_id,
        domain=ProductDomain.surface_treatment,
    )


def _save(session, plan_id: str, project_id: str | None) -> None:
    doe_plan_store.save(session, _plan(plan_id), project_id=project_id)
    session.commit()


def _seed(session_factory) -> None:
    with session_factory() as s:
        _save(s, "pa-1", "proj-A")
        _save(s, "pa-2", "proj-A")
        _save(s, "pb-1", "proj-B")
        _save(s, "legacy-1", None)


def _ids(items: list[dict]) -> set[str]:
    return {i["plan_id"] for i in items}


def test_save_stamps_project_id(session_factory):
    _seed(session_factory)
    with session_factory() as s:
        items, total = doe_plan_store.list_history(s, project_id="proj-A")
    assert _ids(items) == {"pa-1", "pa-2"}
    assert total == 2


def test_project_filter_excludes_other_project_and_legacy(session_factory):
    _seed(session_factory)
    with session_factory() as s:
        items_b, total_b = doe_plan_store.list_history(s, project_id="proj-B")
        assert _ids(items_b) == {"pb-1"}
        assert total_b == 1
        # Unfiltered (back-compat for internal callers) still sees everything.
        items_all, total_all = doe_plan_store.list_history(s)
        assert _ids(items_all) == {"pa-1", "pa-2", "pb-1", "legacy-1"}
        assert total_all == 4


def test_project_filter_combines_with_campaign(session_factory):
    _seed(session_factory)
    with session_factory() as s:
        items, _ = doe_plan_store.list_history(
            s, campaign_id=999, project_id="proj-A"
        )
        assert items == []


def test_history_endpoint_fail_closed_on_empty_project():
    from app.api import doe as doe_api

    with pytest.raises(HTTPException) as ei:
        doe_api.doe_history(project_id="  ")
    assert ei.value.status_code == 422


def test_history_endpoint_project_id_is_required():
    """project_id uses the same required-Query shape as /doe/cycle-runs."""
    from fastapi.params import Query

    from app.api import doe as doe_api

    sig = inspect.signature(doe_api.doe_history)
    default = sig.parameters["project_id"].default
    assert isinstance(default, Query)
    # Same marker shape as the established fail-closed cycle-runs endpoint.
    cycle_sig = inspect.signature(doe_api.doe_cycle_runs)
    assert type(default) is type(cycle_sig.parameters["project_id"].default)


def test_history_endpoint_scopes_to_project(session_factory, monkeypatch):
    from app.api import doe as doe_api

    _seed(session_factory)
    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: session_factory
    )
    resp = doe_api.doe_history(
        project_id="proj-A", campaign_id=None, page=1, page_size=20
    )
    assert {i["plan_id"] for i in resp.items} == {"pa-1", "pa-2"}
    assert resp.total == 2
