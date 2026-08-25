"""DOE plan history: save/load with round provenance + paginated listing."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import doe_plan_store
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import Campaign
from app.domain.schemas import DOEPlan, DOEFactor, DOERun, ProductDomain


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/doe.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def campaign_id(session_factory) -> int:
    with session_factory() as session:
        c = Campaign(name="test-campaign")
        session.add(c)
        session.commit()
        return c.id


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _plan(plan_id: str, design: str = "lhs") -> DOEPlan:
    return DOEPlan(
        design=design,
        factors=[DOEFactor(name="x", low=0.0, high=1.0)],
        runs=[DOERun(run_id=1, coded={"x": 0.0}, natural={"x": 0.0})],
        plan_id=plan_id,
        domain=ProductDomain.surface_treatment,
    )


def _save(session, plan: DOEPlan, **kw) -> None:
    """save() only flushes (caller commits); mirror the API's commit_session."""
    doe_plan_store.save(session, plan, **kw)
    session.commit()


def test_save_then_load(session_factory, campaign_id):
    with session_factory() as session:
        _save(session, _plan("p1"), campaign_id=campaign_id, round_no=3)

    with session_factory() as session:
        loaded = doe_plan_store.load(session, "p1")
        assert loaded is not None
        assert loaded.plan_id == "p1"
        assert loaded.design == "lhs"


def test_load_missing_returns_none(session_factory):
    with session_factory() as session:
        assert doe_plan_store.load(session, "nope") is None


def test_list_history_pagination(session_factory, campaign_id):
    with session_factory() as session:
        for i in range(5):
            _save(session, _plan(f"p{i}"), campaign_id=campaign_id, round_no=i + 1)

    with session_factory() as session:
        items, total = doe_plan_store.list_history(
            session, campaign_id=campaign_id, page=1, page_size=2
        )
        assert total == 5
        assert len(items) == 2
        assert "round" in items[0]
        assert items[0]["round"] is not None


def test_list_history_campaign_filter(session_factory, campaign_id):
    with session_factory() as session:
        other = Campaign(name="other")
        session.add(other)
        session.commit()
        other_id = other.id

    with session_factory() as session:
        _save(session, _plan("a"), campaign_id=campaign_id, round_no=1)
        _save(session, _plan("b"), campaign_id=other_id, round_no=1)

    with session_factory() as session:
        items, total = doe_plan_store.list_history(session, campaign_id=campaign_id)
        assert total == 1
        assert items[0]["plan_id"] == "a"


def test_list_history_includes_orphans(session_factory):
    # 无 campaign_id 的孤立记录，全局查询（campaign_id=None）能看到
    with session_factory() as session:
        _save(session, _plan("orphan"))

    with session_factory() as session:
        items, total = doe_plan_store.list_history(session)
        assert total == 1
        assert items[0]["campaign_id"] is None
        assert items[0]["round"] is None
