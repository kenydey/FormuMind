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


def test_template_endpoint():
    r = client.get("/api/templates/surface_treatment")
    assert r.status_code == 200
    assert r.json()["domain"] == "surface_treatment"
