"""Phase C: MCP JSON / GitHub import with dry_run → confirm."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.db.database import make_engine
from app.main import app
from app.services import mcp_import as mi


CLAUDE_JSON = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {"FOO": "bar"},
        }
    }
}


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "mcp.db"
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("FORMUMIND_MCP_CLIENT_ENABLED", "false")
    monkeypatch.setattr(mi, "_data_root", lambda: tmp_path / "data")
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


def test_parse_claude_desktop_shape():
    prev = mi.parse_mcp_config(CLAUDE_JSON)
    assert not prev.errors
    assert len(prev.servers) == 1
    assert prev.servers[0].id == "filesystem"
    assert prev.servers[0].command == "npx"
    assert prev.servers[0].enabled is False
    assert "FOO" in prev.servers[0].env_keys


def test_reject_non_stdio():
    prev = mi.parse_mcp_config(
        {"mcpServers": {"x": {"command": "npx", "transport": "sse"}}}
    )
    assert prev.errors
    assert any("stdio" in e for e in prev.errors)


def test_import_dry_run_confirm_without_client_flag(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/connectors/mcp/import",
        json={"config": CLAUDE_JSON, "dry_run": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["dry_run"]
    assert body["import_id"]
    assert body["preview"]["servers"][0]["id"] == "filesystem"

    # PUT still blocked when flag off
    assert client.put(
        "/api/connectors/mcp",
        json={"servers": [{"id": "x", "command": "echo"}]},
    ).status_code == 503

    r2 = client.post(
        "/api/connectors/mcp/import/confirm",
        json={"import_id": body["import_id"]},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["imported"] is True
    mcp = r2.json()["mcp"]
    assert any(s["id"] == "filesystem" and s["enabled"] is False for s in mcp)

    listed = client.get("/api/connectors").json()["mcp"]
    assert any(s["id"] == "filesystem" for s in listed)

    # enable without client flag
    r3 = client.patch("/api/connectors/mcp/filesystem", json={"enabled": True})
    assert r3.status_code == 200
    assert next(s for s in r3.json()["mcp"] if s["id"] == "filesystem")["enabled"] is True

    r4 = client.delete("/api/connectors/mcp/filesystem")
    assert r4.status_code == 200
    assert all(s["id"] != "filesystem" for s in r4.json()["mcp"])


def test_github_import_mocked(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_fetch(url: str) -> bytes:
        if url.endswith("/mcp.json"):
            return json.dumps(CLAUDE_JSON).encode()
        raise ValueError(f"404 {url}")

    monkeypatch.setattr(mi, "_default_fetch", fake_fetch)
    r = client.post(
        "/api/connectors/mcp/import/github",
        json={"url": "acme/mcp-pack", "dry_run": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] is True
    assert r.json()["preview"]["source"] == "github"


def test_upload_json(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    raw = json.dumps(CLAUDE_JSON).encode()
    r = client.post(
        "/api/connectors/mcp/import/upload",
        files={"file": ("mcp.json", raw, "application/json")},
        params={"dry_run": "false"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] is True


def test_shell_command_warns_but_allows():
    prev = mi.parse_mcp_config(
        {"mcpServers": {"sh": {"command": "bash", "args": ["-c", "echo hi"]}}}
    )
    assert not prev.errors
    assert any("shell" in w.lower() or "-c" in w for w in prev.warnings)
