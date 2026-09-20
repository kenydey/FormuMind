"""API tests for Dim-5 formulation skills."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.database import make_engine
from app.main import app


def test_list_formulation_skills(tmp_path, monkeypatch):
    db_path = tmp_path / "skills.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_path.as_posix()}")
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    make_engine(f"sqlite:///{db_path.as_posix()}")
    client = TestClient(app)

    r = client.get("/api/formulation-skills")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 3
    ids = {s["id"] for s in body}
    assert "silane_recommend" in ids
    assert "ccd_doe_screen" in ids
    sample = next(s for s in body if s["id"] == "silane_recommend")
    assert sample["action"] == "recommend"
    assert sample["modal"] == "recommend"
    assert "kb_hybrid" in sample["tools"]
    assert sample["checklist"][0]["id"] == "retrieve"
    assert sample["presets"].get("prefer_materials_catalog") is True


def test_get_formulation_skill(tmp_path, monkeypatch):
    db_path = tmp_path / "skills2.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_path.as_posix()}")
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    make_engine(f"sqlite:///{db_path.as_posix()}")
    client = TestClient(app)

    r = client.get("/api/formulation-skills/bayesian_optimize")
    assert r.status_code == 200
    assert r.json()["modal"] == "optimize"


def test_missing_skill_404(tmp_path, monkeypatch):
    db_path = tmp_path / "skills3.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_path.as_posix()}")
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    make_engine(f"sqlite:///{db_path.as_posix()}")
    client = TestClient(app)

    assert client.get("/api/formulation-skills/nope").status_code == 404
