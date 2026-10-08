"""Tests for POST /api/formulations/recommend."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.schemas import ObjectiveSpec, ProductDomain, Requirement
from app.main import app

client = TestClient(app)


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=800,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", weight=0.6, direction="maximize"),
            ObjectiveSpec(metric="cost_cny_per_kg", weight=0.4, direction="minimize"),
        ],
    )


def test_recommend_formulations_offline():
    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 3},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["engine"] == "offline"
    assert len(body["formulas"]) >= 1
    assert len(body["scored"]) >= 1
    comp = body["formulas"][0]["components"][0]
    assert "name" in comp
    assert "cas_no" in comp or comp.get("mf")


def test_recommend_formulations_explicit_objectives():
    res = client.post(
        "/api/formulations/recommend",
        json={
            "requirement": Requirement(domain=ProductDomain.degreaser).model_dump(),
            "objectives": [
                {"metric": "cleaning_efficiency", "weight": 1.0, "direction": "maximize"},
            ],
            "n": 2,
        },
    )
    assert res.status_code == 200
    assert len(res.json()["formulas"]) <= 2


def test_recommend_formulations_invalid_n():
    res = client.post(
        "/api/formulations/recommend",
        json={"requirement": _req().model_dump(), "n": 0},
    )
    assert res.status_code == 422


def test_resolve_request_objectives_normalizes_alias():
    """v25: 显式 objectives 的 metric 别名必须被规范化（v23 第 4 处绕过）。

    用户传 objectives=[{"metric": "salt spray"}] 时必须解析为 salt_spray_hours，
    否则下游 multi_objective_score 按别名取 0.0，静默打出错误排序。
    """
    from types import SimpleNamespace

    from app.api.formulations import _resolve_request_objectives
    from app.domain.schemas import ObjectiveSpec

    body = SimpleNamespace(
        objectives=[ObjectiveSpec(metric="salt spray", direction="maximize")],
        requirement=SimpleNamespace(),  # 非空 objectives 应短路，不被调用
    )
    out = _resolve_request_objectives(body)
    assert out[0].metric == "salt_spray_hours"


def test_recommend_formulations_unknown_metric_422():
    """v27 P1-8: 显式 objectives 传非法 metric → 422，不再静默 0 分错排。"""
    res = client.post(
        "/api/formulations/recommend",
        json={
            "requirement": Requirement(domain=ProductDomain.degreaser).model_dump(),
            "objectives": [
                {"metric": "not_a_real_metric_xyz", "weight": 1.0, "direction": "maximize"},
            ],
            "n": 2,
        },
    )
    assert res.status_code == 422
    assert "not_a_real_metric_xyz" in res.json()["detail"]


def test_recommend_formulations_alias_metric_still_ok():
    """v27 P1-8: 别名 metric（salt spray）解析后合法 → 200（v26 别名修复不退化）。"""
    res = client.post(
        "/api/formulations/recommend",
        json={
            "requirement": Requirement(domain=ProductDomain.anticorrosion_coating).model_dump(),
            "objectives": [
                {"metric": "salt spray", "weight": 1.0, "direction": "maximize"},
            ],
            "n": 2,
        },
    )
    assert res.status_code == 200
