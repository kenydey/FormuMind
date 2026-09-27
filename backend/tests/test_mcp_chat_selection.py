"""Tests for MCP→chat skill-docs, selection, writeish deny, session approve."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import mcp_client, mcp_session_grants, mcp_skill_docs, mcp_chat_tools


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_skill_docs, "_skills_root", lambda: tmp_path / "skills")
    monkeypatch.setattr(
        mcp_session_grants, "_persist_path", lambda: tmp_path / "mcp_session_grants.json"
    )
    # reset in-memory grants
    mcp_session_grants._GRANTS.clear()
    mcp_session_grants._loaded = True
    return tmp_path


def test_ensure_mcp_skill_docs(tmp_data, monkeypatch):
    monkeypatch.setattr(
        "app.services.mcp_client.list_mcp_servers",
        lambda: [
            {
                "id": "fs",
                "command": "npx",
                "args": ["-y", "x"],
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.mcp_client.probe_server",
        lambda s, **k: {"ok": True, "tools": ["read_file", "write_file"], "error": None},
    )

    class S:
        mcp_client_enabled = True

    rows = mcp_skill_docs.ensure_mcp_skill_docs(server_ids=["fs"], settings=S(), probe=True)
    assert rows and rows[0]["skill_id"] == "mcp-fs"
    path = Path(rows[0]["path"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "read_file" in text
    assert "name: mcp-fs" in text


def test_writeish_denied_without_grant(tmp_data, monkeypatch):
    monkeypatch.setattr(
        mcp_client,
        "_prefs_servers",
        lambda: [
            {
                "id": "fs",
                "command": "echo",
                "args": [],
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ],
    )

    class S:
        mcp_client_enabled = True

    out = mcp_client.call_tool_with_session(
        "fs", "write_file", {}, session_id="sess-1", settings=S()
    )
    assert out["ok"] is False
    assert out.get("mcp_permission_required")


def test_approve_then_call(tmp_data, monkeypatch):
    called = {"ok": False}

    monkeypatch.setattr(
        mcp_client,
        "_prefs_servers",
        lambda: [
            {
                "id": "fs",
                "command": "echo",
                "args": [],
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ],
    )

    def fake_call(server_id, tool_name, arguments=None):
        called["ok"] = True
        return {"ok": True, "result": {"content": "done"}}

    monkeypatch.setattr(mcp_client, "_call_tool", fake_call)

    class S:
        mcp_client_enabled = True

    mcp_session_grants.approve_session("sess-2", "fs", tool_name="write_file")
    out = mcp_client.call_tool_with_session(
        "fs", "write_file", {}, session_id="sess-2", settings=S()
    )
    assert out["ok"] is True
    assert called["ok"] is True


def test_maybe_run_explicit_mcp_permission(tmp_data, monkeypatch):
    monkeypatch.setattr(
        "app.services.mcp_client.list_mcp_servers",
        lambda: [
            {
                "id": "fs",
                "command": "echo",
                "args": [],
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.mcp_client.probe_server",
        lambda s, **k: {"ok": True, "tools": ["write_file"]},
    )

    class S:
        mcp_client_enabled = True

    out = mcp_chat_tools.maybe_run_selected_mcp(
        "/mcp fs write_file {}",
        ["fs"],
        session_id=None,
        settings=S(),
    )
    assert out["mcp_permission_required"]
    assert out["mcp_permission_required"]["tool_name"] == "write_file"


def test_mcp_prompt_block_injects(tmp_data, monkeypatch):
    monkeypatch.setattr(
        "app.services.mcp_client.list_mcp_servers",
        lambda: [
            {
                "id": "demo",
                "command": "x",
                "args": [],
                "enabled": True,
                "env": {},
                "transport": "stdio",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.mcp_client.probe_server",
        lambda s, **k: {"ok": True, "tools": ["search"], "error": None},
    )
    monkeypatch.setattr(
        "app.services.skills_store.is_enabled",
        lambda sid, **k: True,
    )

    class S:
        mcp_client_enabled = True

    # skill_prompt_block reads ./data/skills — mirror docs there for this test.
    data_skills = Path("./data").resolve() / "skills" / "mcp-demo"
    data_skills.mkdir(parents=True, exist_ok=True)
    rows = mcp_skill_docs.ensure_mcp_skill_docs(
        server_ids=["demo"], settings=S(), probe=True
    )
    # Copy generated skill into the path chat_skills discovers
    src = Path(rows[0]["path"])
    dest = data_skills / "SKILL.md"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (data_skills / ".formumind-install.json").write_text(
        '{"origin":"pack","source":"mcp","server_id":"demo"}',
        encoding="utf-8",
    )
    try:
        block = mcp_skill_docs.mcp_prompt_block(["demo"], settings=S())
        assert "mcp-demo" in block or "MCP" in block
    finally:
        dest.unlink(missing_ok=True)
        (data_skills / ".formumind-install.json").unlink(missing_ok=True)
