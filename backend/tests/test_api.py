import time

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_meta_lists_domains():
    r = client.get("/api/meta")
    assert r.status_code == 200
    assert "anticorrosion_coating" in r.json()["domains"]


def test_ingredients_endpoint():
    r = client.get("/api/ingredients")
    assert r.status_code == 200
    data = r.json()
    assert "Xylene" in data
    assert data["Xylene"]["price_cny_per_kg"] == 8.0
    assert data["Xylene"]["voc_contrib"] == 1.0
    assert "Deionized water" in data
    assert data["Deionized water"]["voc_contrib"] == 0.0


def test_research_endpoint():
    payload = {"domain": "degreaser", "substrate": "carbon_steel", "cleaning_efficiency": 95, "ph_target": 12.5}
    r = client.post("/api/research", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["recommended"]
    assert body["evidence"]


def test_doe_endpoint():
    payload = {"domain": "anticorrosion_coating", "cure_temperature_c": 90}
    r = client.post("/api/doe?design=plackett_burman", json=payload)
    assert r.status_code == 200
    assert r.json()["design"] == "plackett_burman"
    assert r.json()["runs"]


def test_optimize_then_poll():
    payload = {"requirement": {"domain": "anticorrosion_coating", "salt_spray_hours": 500}, "iterations": 8}
    r = client.post("/api/optimize", json=payload)
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    # Poll until completion (in-process thread; should be fast).
    for _ in range(50):
        s = client.get(f"/api/tasks/{task_id}")
        assert s.status_code == 200
        state = s.json()["state"]
        if state in ("completed", "failed"):
            break
        time.sleep(0.05)
    body = client.get(f"/api/tasks/{task_id}").json()
    assert body["state"] == "completed"
    assert body["result"]["top_formulations"]


def test_unknown_task_404():
    r = client.get("/api/tasks/does-not-exist")
    assert r.status_code == 404


def test_search_task_survives_process_restart(monkeypatch, tmp_path):
    """Simulate uvicorn --reload: in-memory registry cleared but poll still works."""
    from app.worker import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "_TASK_PERSIST_DIR", tmp_path)

    r = client.post(
        "/api/search/stream",
        json={
            "query": "epoxy zinc phosphate",
            "source_types": ["patents"],
            "requirement": {
                "domain": "anticorrosion_coating",
                "substrate": "carbon_steel",
                "salt_spray_hours": 500,
                "film_weight_gsm": 70,
                "cure_temperature_c": 80,
                "cleaning_efficiency": 90,
                "voc_limit_gpl": 420,
                "ph_target": None,
                "notes": "",
                "objectives": [],
            },
        },
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]

    # Simulate process restart: wipe in-memory task registry.
    tasks_mod.task_manager._tasks.clear()

    # Poll must not 404 — task was persisted to disk on registration.
    poll = client.get(f"/api/tasks/{task_id}")
    assert poll.status_code == 200, poll.text
    assert poll.json()["task_id"] == task_id


def test_template_endpoint():
    r = client.get("/api/templates/surface_treatment")
    assert r.status_code == 200
    assert r.json()["domain"] == "surface_treatment"


def test_experiment_feedback_trains_model():
    from app.services.training import registry

    registry.reset(persist=True)
    try:
        records = [
            {
                "domain": "anticorrosion_coating",
                "factors": {"Zinc phosphate": z, "Bisphenol-A epoxy (DGEBA)": 38, "Polyamide hardener": 14},
                "cure_temperature_c": 80,
                "measured": {"salt_spray_hours": 200 + 80 * z},
            }
            for z in [3, 5, 7, 9, 11, 13]
        ]
        r = client.post("/api/experiments", json={"records": records, "retrain": True})
        assert r.status_code == 200
        report = r.json()
        assert report["total_records"] == 6
        assert any(m["metric"] == "salt_spray_hours" for m in report["trained"])

        models = client.get("/api/models").json()
        assert any(m["domain"] == "anticorrosion_coating" for m in models)

        # Force-retrain endpoint also works.
        assert client.post("/api/train").json()["trained"]
    finally:
        registry.reset(persist=True)


def test_experiment_below_threshold_reports_no_model():
    from app.services.training import registry

    registry.reset(persist=True)
    try:
        records = [{
            "domain": "degreaser",
            "factors": {"Nonionic surfactant (C12-14 EO7)": 4},
            "measured": {"cleaning_efficiency": 90},
        }]
        report = client.post("/api/experiments", json={"records": records}).json()
        assert report["trained"] == []
        assert "Need" in report["message"]
    finally:
        registry.reset(persist=True)
