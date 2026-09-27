"""Tests for W1-6: MCP skill-doc rendering (descriptors, examples, naming, stale cleanup)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services import mcp_skill_docs


@pytest.fixture()
def tmp_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_skill_docs, "_skills_root", lambda: tmp_path / "skills")
    return tmp_path / "skills"


def _server(sid: str = "chem", **kw) -> dict:
    d = {
        "id": sid,
        "command": "echo",
        "args": [],
        "enabled": True,
        "env": {},
        "transport": "stdio",
    }
    d.update(kw)
    return d


def _probe_ok(descriptors: list[dict]) -> dict:
    return {
        "ok": True,
        "tools": descriptors,
        "names": [d["name"] for d in descriptors],
        "error": None,
    }


class _Settings:
    mcp_client_enabled = True


def _mock_probe(monkeypatch: pytest.MonkeyPatch, servers: list[dict], probed: dict):
    monkeypatch.setattr(
        "app.services.mcp_client.list_mcp_servers", lambda: list(servers)
    )
    monkeypatch.setattr(
        "app.services.mcp_client.probe_server", lambda s, **k: dict(probed)
    )


def _descriptor(name="search_compounds", **kw) -> dict:
    d = {
        "name": name,
        "description": "按名称搜索化合物。",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "化合物名称"},
                "limit": {"type": "integer", "default": 10},
                "mode": {"type": "string", "enum": ["deep", "fast"], "default": "fast"},
                "internal_debug": {"type": "boolean"},
            },
        },
    }
    d.update(kw)
    return d


def _example_block(text: str) -> dict:
    """提取文档中 **Example**: 后的 JSON 块。"""
    tail = text.split("**Example**:", 1)[1]
    start = tail.index("{")
    end = tail.index("```", start)
    return json.loads(tail[start:end])


def test_render_description_schema_example(tmp_skills, monkeypatch):
    desc = _descriptor()
    _mock_probe(monkeypatch, [_server()], _probe_ok([desc]))
    rows = mcp_skill_docs.ensure_mcp_skill_docs(
        server_ids=["chem"], settings=_Settings(), probe=True
    )
    assert rows and rows[0]["ok"] is True
    text = Path(rows[0]["path"]).read_text(encoding="utf-8")

    # 工具节：名称 + 描述
    assert "### search_compounds" in text
    assert "按名称搜索化合物" in text
    # Input：仅 required + 含 default 的字段
    assert "`query`" in text and "`limit`" in text and "`mode`" in text
    assert "internal_debug" not in text
    assert "default=10" in text
    assert 'enum=["deep", "fast"]' in text
    # 调用约束段
    assert "绝不猜测工具名" in text
    assert "raw HTTP" in text

    # Example 无虚构字段：keys ⊆ schema properties
    example = _example_block(text)
    schema_props = set(desc["inputSchema"]["properties"])
    assert set(example) <= schema_props
    assert set(example) == {"query", "limit", "mode"}
    # 示例值优先级：enum[0] → default → 类型占位
    assert example["mode"] == "deep"  # enum[0]，不是 default 的 "fast"
    assert example["limit"] == 10  # default
    assert example["query"] == "string"  # 类型占位


def test_trigger_description_from_use_when(tmp_skills, monkeypatch):
    desc = _descriptor()
    _mock_probe(
        monkeypatch, [_server(useWhen="查询化学品毒性数据"), _server("other")], _probe_ok([desc])
    )
    rows = mcp_skill_docs.ensure_mcp_skill_docs(settings=_Settings(), probe=True)
    by_id = {r["server_id"]: r for r in rows}
    text = Path(by_id["chem"]["path"]).read_text(encoding="utf-8")
    assert "查询化学品毒性数据" in text
    other = Path(by_id["other"]["path"]).read_text(encoding="utf-8")
    assert "当用户需要该 MCP 服务器提供的外部工具能力时" in other


def test_safe_id_normalized(tmp_skills, monkeypatch):
    assert mcp_skill_docs._safe_id("My Server.1") == "my-server-1"
    assert re.match(r"^[a-z0-9-]+$", mcp_skill_docs._safe_id("My Server.1"))
    desc = _descriptor()
    _mock_probe(monkeypatch, [_server("My Server.1")], _probe_ok([desc]))
    rows = mcp_skill_docs.ensure_mcp_skill_docs(settings=_Settings(), probe=True)
    assert rows[0]["skill_id"] == "mcp-my-server-1"
    assert Path(rows[0]["path"]).is_file()


def test_case_collision_does_not_overwrite(tmp_skills, monkeypatch):
    desc = _descriptor()
    # 先为 "Chem" 生成文档
    _mock_probe(monkeypatch, [_server("Chem")], _probe_ok([desc]))
    rows1 = mcp_skill_docs.ensure_mcp_skill_docs(
        server_ids=["Chem"], settings=_Settings(), probe=True
    )
    assert rows1[0]["ok"] is True
    path = Path(rows1[0]["path"])
    original = path.read_text(encoding="utf-8")

    # "chem" 与 "Chem" 折叠到同一 skill id → 跳过，不覆盖
    _mock_probe(monkeypatch, [_server("Chem"), _server("chem")], _probe_ok([desc]))
    rows = mcp_skill_docs.ensure_mcp_skill_docs(settings=_Settings(), probe=True)
    by_id = {r["server_id"]: r for r in rows}
    assert by_id["Chem"]["ok"] is True
    assert by_id["chem"]["ok"] is False
    assert "冲突" in (by_id["chem"]["error"] or "")
    assert path.read_text(encoding="utf-8") == original  # 未被覆盖


def test_probe_failure_removes_stale_dir(tmp_skills, monkeypatch):
    desc = _descriptor()
    _mock_probe(monkeypatch, [_server()], _probe_ok([desc]))
    rows = mcp_skill_docs.ensure_mcp_skill_docs(
        server_ids=["chem"], settings=_Settings(), probe=True
    )
    assert rows[0]["ok"] is True
    stale_dir = tmp_skills / "mcp-chem"
    assert (stale_dir / "SKILL.md").is_file()

    # probe 失败 → 旧目录被删除，不写新文档
    _mock_probe(
        monkeypatch,
        [_server()],
        {"ok": False, "tools": [], "names": [], "error": "connection refused"},
    )
    rows = mcp_skill_docs.ensure_mcp_skill_docs(
        server_ids=["chem"], settings=_Settings(), probe=True
    )
    assert rows[0]["ok"] is False
    assert "connection refused" in (rows[0]["error"] or "")
    assert not stale_dir.exists()


def test_old_shape_tools_as_names_still_renders(tmp_skills, monkeypatch):
    """兼容旧 shape：probe 返回 tools=[名称字符串] 时仍渲染工具名。"""
    _mock_probe(
        monkeypatch,
        [_server()],
        {"ok": True, "tools": ["read_file", "write_file"], "error": None},
    )
    rows = mcp_skill_docs.ensure_mcp_skill_docs(
        server_ids=["chem"], settings=_Settings(), probe=True
    )
    assert rows[0]["ok"] is True
    text = Path(rows[0]["path"]).read_text(encoding="utf-8")
    assert "### read_file" in text
    assert "### write_file" in text
