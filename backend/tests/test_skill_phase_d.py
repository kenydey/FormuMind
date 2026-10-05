"""Phase D: skill packs, update check, slash-related catalog."""
from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from app.db.database import make_engine
from app.main import app
from app.services import skill_install as si


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "d.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(si, "_data_root", lambda: tmp_path / "data")
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg
    from app.services import skills_store

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(skills_store, "_prefs_path", lambda: tmp_path / "skills_prefs.json")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    make_engine(f"sqlite:///{db_path.as_posix()}")
    return TestClient(app)


def test_list_and_install_coatings_silane_pack(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/skills/packs")
    assert r.status_code == 200
    packs = {p["id"]: p for p in r.json()["packs"]}
    assert "coatings-silane" in packs
    assert packs["coatings-silane"]["installed"] is False

    dry = client.post("/api/skills/packs/coatings-silane/install", json={"dry_run": True})
    assert dry.status_code == 200, dry.text
    assert dry.json()["install_id"]
    conf = client.post(
        "/api/skills/install/confirm",
        json={"install_id": dry.json()["install_id"]},
    )
    assert conf.status_code == 200, conf.text
    ids = {s["id"] for s in conf.json()["catalog"]["skills"]}
    assert "coatings-silane" in ids
    # not a default playbook
    playbooks = [s for s in conf.json()["catalog"]["skills"] if s["kind"] == "playbook"]
    assert all(s["id"] != "coatings-silane" for s in playbooks)
    assert all(s["id"] != "silane_recommend" for s in playbooks)

    packs2 = {p["id"]: p for p in client.get("/api/skills/packs").json()["packs"]}
    assert packs2["coatings-silane"]["installed"] is True


def test_check_update_github_skill(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    md_v1 = """---
name: remote-skill
description: v1 skill for update test
summary: v1
allowed_tools: kb_hybrid
---
body v1
"""
    md_v2 = md_v1.replace("v1", "v2")

    state = {"sha": "aaa111", "md": md_v1}

    def fake_fetch(url: str) -> bytes:
        if "api.github.com" in url:
            payload = {
                "encoding": "base64",
                "content": base64.b64encode(state["md"].encode()).decode(),
                "sha": state["sha"],
            }
            return json.dumps(payload).encode()
        raise AssertionError(url)

    monkeypatch.setattr(si, "_default_fetch", fake_fetch)
    inst = client.post(
        "/api/skills/install/github",
        json={"url": "acme/skills/remote-skill", "dry_run": False},
    )
    assert inst.status_code == 200, inst.text

    chk = client.get("/api/skills/installed/remote-skill/check-update")
    assert chk.status_code == 200
    assert chk.json()["checkable"] is True
    assert chk.json()["update_available"] is False

    state["sha"] = "bbb222"
    state["md"] = md_v2
    chk2 = client.get("/api/skills/installed/remote-skill/check-update")
    assert chk2.json()["update_available"] is True

    upd = client.post(
        "/api/skills/installed/remote-skill/update",
        json={"dry_run": False},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["installed"] is True
    text = (tmp_path / "data" / "skills" / "remote-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "v2" in text
