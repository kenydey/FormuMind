"""P2: cover GET /api/org/dashboard (previously zero API coverage)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_org_dashboard_shape():
    with TestClient(app) as client:
        res = client.get("/api/org/dashboard")
    assert res.status_code == 200
    body = res.json()
    for key in (
        "total_experiments",
        "total_campaigns",
        "total_projects",
        "active_projects",
        "by_domain",
        "top_performers",
        "ingredient_frequency",
        "convergence_rate",
        "avg_rounds_to_converge",
        "recent_activity",
    ):
        assert key in body
    assert isinstance(body["by_domain"], dict)
    assert isinstance(body["top_performers"], list)
    assert isinstance(body["ingredient_frequency"], list)
    assert isinstance(body["recent_activity"], dict)
    assert "experiments_added" in body["recent_activity"]
    assert "campaigns_created" in body["recent_activity"]
    assert isinstance(body["total_experiments"], int)
    assert 0.0 <= float(body["convergence_rate"]) <= 1.0
