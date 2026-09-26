"""Skills / Evidence / Connectors / MCP upgrade smoke tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.database import make_engine
from app.main import app
from app.services.scholar_helpers import extract_dois, verify_dois
from app.services.chat_skills import list_chat_skills, skill_prompt_block
from app.services.evidence_synthesis import enrich_chat_prompt, LITERATURE_DISCIPLINE
from app.services.mcp_client import _is_writeish


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "skills_ev.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("FORMUMIND_MCP_CLIENT_ENABLED", "false")
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg
    from app.services import skills_store

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(skills_store, "_prefs_path", lambda: tmp_path / "skills_prefs.json")
    make_engine(f"sqlite:///{db_path.as_posix()}")
    return TestClient(app)


def test_skills_catalog_includes_playbooks_and_chat_skills(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    kinds = {s["kind"] for s in body["skills"]}
    assert "playbook" in kinds
    assert "chat_skill" in kinds
    ids = {s["id"] for s in body["skills"]}
    assert "literature-review" in ids
    assert "silane_recommend" in ids


def test_skills_prefs_disable(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/skills/prefs", json={"disabled_ids": ["method-writer"]})
    assert r.status_code == 200
    skills = {s["id"]: s for s in r.json()["skills"]}
    assert skills["method-writer"]["enabled"] is False
    assert skills["literature-review"]["enabled"] is True


def test_chat_skills_prompt_block():
    skills = list_chat_skills(include_body=False)
    assert any(s["id"] == "literature-review" for s in skills)
    block = skill_prompt_block(["literature-review"])
    assert "Retrieve first" in block or "先检索" in block or "literature" in block.lower()


def test_enrich_prompt_evidence_mode():
    class S:
        evidence_synthesis_mode = "off"
        chat_skills_runtime_enabled = True

    out = enrich_chat_prompt("BASE", mode="evidence", skill_ids=["literature-review"], settings=S())
    assert "BASE" in out
    assert "Evidence Synthesis" in out or LITERATURE_DISCIPLINE[:20] in out


def test_extract_and_verify_dois_offline():
    text = "See 10.1000/xyz123 and also 10.1038/nature12345."
    dois = extract_dois(text)
    assert len(dois) >= 2
    # verify without network dependency — mock by disabling
    rows = verify_dois(dois, enabled=False)
    assert all(r["status"] == "skipped" for r in rows)


def test_mcp_writeish_heuristic():
    assert _is_writeish("write_file")
    assert _is_writeish("shell_exec")
    assert not _is_writeish("search_papers")


def test_connectors_list(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.get("/api/connectors")
    assert r.status_code == 200
    body = r.json()
    assert any(c["id"] == "literature" for c in body["builtin"])
    assert body["mcp_client_enabled"] is False


def test_mcp_disabled_rejects_put(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.put(
        "/api/connectors/mcp",
        json={"servers": [{"id": "x", "command": "echo", "args": []}]},
    )
    assert r.status_code == 503


def test_chat_accepts_mode_and_skills(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.api.chat.answer_question",
        lambda *a, **k: ("ok with 10.1000/test", []),
    )
    monkeypatch.setattr("app.api.chat._augment_with_kb", lambda *a, **k: ([], 0, None, None))
    monkeypatch.setattr("app.api.chat.detect_clarification", lambda *a, **k: None)
    monkeypatch.setattr("app.api.chat.build_sourced_claims", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.evidence_synthesis.try_paperqa_answer",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.scholar_helpers.verify_dois",
        lambda *a, **k: [{"doi": "10.1000/test", "status": "skipped", "title": None, "retracted": None}],
    )
    r = client.post(
        "/api/chat",
        json={
            "question": "silane coupling evidence?",
            "sources": [
                {
                    "source": "test",
                    "identifier": "1",
                    "title": "t",
                    "snippet": "silane",
                    "relevance": 0.9,
                }
            ],
            "mode": "evidence",
            "selected_skills": ["literature-review"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("mode") == "evidence"
    assert "ok" in body["answer"]
