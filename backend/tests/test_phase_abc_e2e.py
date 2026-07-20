"""E2E smoke: optimize → enrich validate → loop → DOE adopt → loop_history badge."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import SqliteCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.db.store import JsonExperimentStore
from app.main import app
from app.services.training import registry

_REQUIREMENT = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "film_weight_gsm": 70,
    "cure_temperature_c": 80,
    "cleaning_efficiency": 90,
    "voc_limit_gpl": 250.0,
    "ph_target": None,
    "notes": "",
    "objectives": [],
}


def _poll(client: TestClient, status_url: str, *, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(status_url).json()
        if last.get("state") in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUMIND_LOOP_CONVERGENCE_ENABLED", "false")
    get_settings.cache_clear()
    db_path = tmp_path / "e2e.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    reset_campaign_store(SqliteCampaignStore(factory))
    store = JsonExperimentStore(str(tmp_path / "exp.json"))
    registry._store = store  # noqa: SLF001
    registry.load()
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_phase_abc_e2e_optimize_enrich_loop_adopt_history():
    client = TestClient(app)

    # 1) 寻优
    opt_handle = client.post(
        "/api/optimize",
        json={"requirement": _REQUIREMENT, "iterations": 6},
    ).json()
    opt_status = _poll(client, opt_handle["status_url"])
    assert opt_status["state"] == "completed", opt_status
    top = opt_status["result"]["top_formulations"]
    assert top, "optimize should yield formulations"

    # 2) enrich validate + requirement warnings (F-2 + F-4)
    form = dict(top[0])
    form["predicted"] = {**(form.get("predicted") or {}), "voc_gpl": 400.0}
    val = client.post(
        "/api/formulations/validate",
        json={"formulations": [form], "requirement": _REQUIREMENT},
    )
    assert val.status_code == 200, val.text
    val_body = val.json()
    assert val_body["formulations"], "validate should return enriched formulation"
    assert any("VOC" in w for w in val_body.get("warnings", [])), val_body.get("warnings")

    # 3) 闭环 iterate
    loop_handle = client.post(
        "/api/loop/iterate",
        json={**_REQUIREMENT, "optimize_iterations": 4, "n_suggest": 2},
    ).json()
    loop_status = _poll(client, loop_handle["status_url"], timeout_s=90.0)
    assert loop_status["state"] == "completed", loop_status
    loop_result = loop_status["result"]
    assert loop_result["optimization"]["top_formulations"]
    next_doe = loop_result["next_doe"]
    assert next_doe["runs"]

    # 4) DOE adopt → 创建实验台账 (L-1)
    adopt = client.post(
        "/api/experiments/workbench/campaigns",
        json={
            "plan": next_doe,
            "strategy": "BayBE-loop-next",
            "requirement": _REQUIREMENT,
        },
    )
    assert adopt.status_code == 200, adopt.text
    campaign_id = adopt.json()["campaign_id"]
    assert campaign_id >= 1
    assert len(adopt.json()["rows"]) == len(next_doe["runs"])

    # 5) 带 campaign_id 再跑一轮闭环 → loop_history 写入 (L-3)
    loop2 = client.post(
        "/api/loop/iterate",
        json={
            **_REQUIREMENT,
            "optimize_iterations": 4,
            "n_suggest": 2,
            "workbench_campaign_id": campaign_id,
        },
    ).json()
    loop2_status = _poll(client, loop2["status_url"], timeout_s=90.0)
    assert loop2_status["state"] == "completed", loop2_status

    wb = client.get(f"/api/experiments/workbench/{campaign_id}")
    assert wb.status_code == 200, wb.text
    history = wb.json().get("loop_history") or []
    assert len(history) >= 1, "loop_history should record at least one closed-loop round"
    assert history[-1].get("rmse_by_metric") is not None or history[-1].get("engine")
