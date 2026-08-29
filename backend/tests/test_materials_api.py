"""Materials API 路由层测试：错误码、参数校验、降级路径。

test_material_store.py 已覆盖 list / upsert / availability 的正常契约与
部分错误码；本文件补 substitutes / supply-risk 的错误路径、409 材料空间
未启用降级与 422 参数校验。
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def _formulation() -> dict:
    return {
        "name": "Waterborne test",
        "domain": "anticorrosion_coating",
        "ingredients": [
            {"name": "Waterborne acrylic emulsion", "role": "resin", "weight_pct": 60.0},
            {"name": "Deionized water", "role": "solvent", "weight_pct": 40.0},
        ],
    }


# ── 422 参数校验 ─────────────────────────────────────────────────────────────


def test_list_materials_rejects_limit_zero():
    r = client.get("/api/materials?limit=0")
    assert r.status_code == 422


def test_list_materials_rejects_limit_over_cap():
    r = client.get("/api/materials?limit=5000")
    assert r.status_code == 422


def test_list_materials_filters_by_query_term():
    r = client.get("/api/materials?q=xylene")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all("xylene" in m["name"].lower() for m in body["materials"])


# ── substitutes 错误路径 ─────────────────────────────────────────────────────


def test_substitutes_requires_formulation_or_requirement():
    r = client.post("/api/materials/substitutes", json={"material": "Xylene"})
    assert r.status_code == 400


def test_substitutes_unknown_material_404():
    r = client.post(
        "/api/materials/substitutes",
        json={"formulation": _formulation(), "material": "Nonexistent Resin"},
    )
    assert r.status_code == 404


def test_substitutes_slot_index_out_of_range():
    r = client.post(
        "/api/materials/substitutes",
        json={"formulation": _formulation(), "slot_index": 99},
    )
    assert r.status_code == 400


def test_substitutes_ok_with_mock():
    with patch(
        "app.services.substitution.find_substitutes",
        return_value={"substitutes": [], "slot": 0},
    ) as mock_fs:
        r = client.post(
            "/api/materials/substitutes",
            json={"formulation": _formulation(), "slot_index": 0, "limit": 5},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"substitutes": [], "slot": 0}
    mock_fs.assert_called_once()
    assert mock_fs.call_args.args[1] == 0  # the resolved slot index


# ── supply-risk ──────────────────────────────────────────────────────────────


def test_supply_risk_unknown_domain_400():
    r = client.get("/api/materials/supply-risk?domain=not_a_domain")
    assert r.status_code == 400


# ── 409 材料空间未启用降级 ───────────────────────────────────────────────────


def test_store_disabled_returns_409(monkeypatch):
    monkeypatch.setenv("FORMUMIND_MATERIAL_STORE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        r = client.post("/api/materials", json={"name": "Xylene", "enrich": False})
        assert r.status_code == 409
        assert "材料空间未启用" in r.text
    finally:
        get_settings.cache_clear()


def test_availability_rejects_invalid_flag():
    r = client.post(
        "/api/materials/availability",
        json={"name": "Xylene", "availability": "nonsense"},
    )
    assert r.status_code == 400
