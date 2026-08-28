"""Phase 1 owner 预埋 — 单 token 恒过，多用户预留."""

from __future__ import annotations

import os
from fastapi import Request
from fastapi.testclient import TestClient


def test_owner_phase1_single_token_always_pass():
    """单 token (default) 下任何资源不触发 403。"""
    from app.middleware.api_auth import assert_owner

    # None owner, default caller → pass
    assert_owner(None, "default") is None
    # 有 owner 但 caller 是 default → 也 pass（Phase 1 软校验）
    assert_owner("alice", "default") is None
    assert_owner(None, "default") is None


def test_owner_phase1_multi_user_enforce():
    """FORMUMIND_MULTI_USER=true 时 owner 不一致应 403。"""
    from app.middleware.api_auth import assert_owner
    from fastapi import HTTPException
    import pytest

    os.environ["FORMUMIND_MULTI_USER"] = "true"
    try:
        # 一致 → pass
        assert_owner("alice", "alice") is None
        # 不一致 → 403
        with pytest.raises(HTTPException) as exc:
            assert_owner("alice", "bob")
        assert exc.value.status_code == 403
        # 资源无 owner → 即使多用户也 pass（历史数据兼容）
        assert_owner(None, "bob") is None
    finally:
        os.environ.pop("FORMUMIND_MULTI_USER", None)


def test_get_current_owner_default(monkeypatch):
    from app.middleware.api_auth import get_current_owner
    from unittest.mock import MagicMock

    req = MagicMock(spec=Request)
    req.headers = {}
    req.query_params = {}
    req.url.path = "/api/experiments/workbench/campaigns"
    req.method = "POST"
    # 单 token 恒 default
    assert get_current_owner(req) == "default"


def test_campaign_owner_persisted(tmp_path, monkeypatch):
    """create_from_plan 写入 owner_id（非 default 时）。"""
    from app.db.database import make_engine, make_session_factory
    from app.db.campaign_store import SqliteCampaignStore
    from app.domain.schemas import DOEPlan, ProductDomain, Requirement
    import asyncio

    db_path = tmp_path / "owner.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    store = SqliteCampaignStore(factory)

    async def _run():
        from app.domain.schemas import DOEFactor, DOEPlan
        plan = DOEPlan(
            plan_id="p1",
            design="lhs",
            domain=ProductDomain.anticorrosion_coating,
            factors=[DOEFactor(name="x", low=0, high=1)],
            runs=[],
        )
        c = await store.create_from_plan(plan, name="t", owner_id="alice")
        assert c.owner_id == "alice"
        c2 = await store.get_campaign(c.id)
        assert c2 is not None and c2.owner_id == "alice"

    asyncio.run(_run())
