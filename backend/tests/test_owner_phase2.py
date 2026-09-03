"""P1 owner Phase2 硬校验：多用户 403 越权 + 公共行放行 + 单用户兼容."""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain, Requirement, ObjectiveSpec
from app.main import app


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0, "cure_temperature_c": 80.0})],
        notes="test",
        plan_id="phase2",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _sqlite(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.delenv("FORMUMIND_MULTI_USER", raising=False)
    monkeypatch.delenv("FORMUMIND_API_TOKENS_JSON", raising=False)
    get_settings.cache_clear()
    yield
    reset_campaign_store(None)
    monkeypatch.delenv("FORMUMIND_MULTI_USER", raising=False)
    monkeypatch.delenv("FORMUMIND_API_TOKENS_JSON", raising=False)
    get_settings.cache_clear()


def _client(tmp_path):
    db = tmp_path / "owner2.db"
    engine = make_engine(f"sqlite:///{db}")
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    return TestClient(app)


def test_soft_mode_no_enforcement(tmp_path):
    # 默认单 token：bob 也能读 alice 的资源（Phase1 恒过）
    client = _client(tmp_path)
    from app.middleware.api_auth import assert_owner

    # soft: current=default -> 无论 resource_owner 均放行
    assert_owner("alice", "default") is None
    # soft: resource 无 owner -> 放行
    assert_owner(None, "bob") is None


def test_hard_mode_forbidden_on_owner_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    from app.middleware.api_auth import assert_owner

    # hard: alice != bob -> 403
    with pytest.raises(Exception) as exc:
        assert_owner("alice", "bob")
    assert "403" in str(exc.value) or "Forbidden" in str(exc.value)


def test_hard_mode_allows_public_row(monkeypatch):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    from app.middleware.api_auth import assert_owner

    # 公共行 owner_id IS NULL -> 放行
    assert_owner(None, "bob") is None
    assert_owner("", "bob") is None


def test_multi_user_end_to_end_alice_cannot_read_bobs_campaign(monkeypatch, tmp_path):
    # 端到端：alice 建 campaign，bob 读应 403，公共行放行
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", json.dumps({"alice": "tok-alice", "bob": "tok-bob"}))
    # 关闭 bearer 校验便于 TestClient 直通（或让映射通过）
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    client = _client(tmp_path)
    # alice 建
    r = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump()},
        headers={"Authorization": "Bearer tok-alice"},
    )
    assert r.status_code == 200, r.text
    cid = r.json()["campaign_id"]
    # bob 读 -> 403
    r2 = client.get(f"/api/experiments/workbench/{cid}", headers={"Authorization": "Bearer tok-bob"})
    assert r2.status_code == 403, r2.text
    # alice 自己读 -> 200
    r3 = client.get(f"/api/experiments/workbench/{cid}", headers={"Authorization": "Bearer tok-alice"})
    assert r3.status_code == 200
    # auth/status 透传 owner
    r4 = client.get("/api/auth/status", headers={"Authorization": "Bearer tok-bob"})
    body = r4.json()
    assert body["multi_user"] is True
    assert body["owner"] == "bob"


def test_single_user_compat_still_default(tmp_path, monkeypatch):
    # 未开启 multi_user 时 owner 恒 default，旧库公共行不受影响
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    client = _client(tmp_path)
    r = client.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()})
    assert r.status_code == 200
    cid = r.json()["campaign_id"]
    # 任意 token 下仍可读（soft）
    r2 = client.get(f"/api/experiments/workbench/{cid}")
    assert r2.status_code == 200
    r3 = client.get("/api/auth/status")
    assert r3.json()["multi_user"] is False
    assert r3.json()["owner"] == "default"
