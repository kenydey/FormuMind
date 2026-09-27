"""W2-7 (P1-3) MCP 逐工具审批三态测试。"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app.services import mcp_approval
from app.services import mcp_client


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "approval.db"
    mcp_approval.ensure_store(path)
    return path


SID, TOOL = "srv1", "read_file"


def test_default_policy_is_ask_deny(db):
    v = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    assert v["verdict"] == "deny"
    assert v["reason"] == "approval_required"
    assert v["request_id"] is not None


def test_allow_policy_permits(db):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", path=db)
    assert mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)["verdict"] == "allow"


def test_block_policy_denies(db):
    mcp_approval.set_policy(SID, TOOL, "block", "global", path=db)
    v = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "policy_block"


def test_block_overrides_allow(db):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", path=db)
    mcp_approval.set_policy(SID, TOOL, "block", "session", path=db)
    v = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "policy_block"


def test_ask_overrides_allow(db):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", path=db)
    mcp_approval.set_policy(SID, TOOL, "ask", "project", path=db)
    v = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "approval_required"


def test_effective_decision_priority():
    assert mcp_approval.effective_decision({}) == "ask"
    assert mcp_approval.effective_decision({"global": "allow"}) == "allow"
    assert mcp_approval.effective_decision({"global": "allow", "session": "ask"}) == "ask"
    assert mcp_approval.effective_decision({"global": "allow", "project": "block"}) == "block"


def test_pending_within_timeout_reuses_request(db):
    v1 = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    v2 = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1100.0)
    assert v2["reason"] == "approval_pending"
    assert v2["request_id"] == v1["request_id"]


def test_ask_timeout_auto_deny(db):
    v1 = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    v2 = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0 + 301.0)
    assert v2["verdict"] == "deny" and v2["reason"] == "approval_timeout"
    # 已过期，再次求值应产生新的 pending 请求
    v3 = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1400.0)
    assert v3["reason"] == "approval_required" and v3["request_id"] != v1["request_id"]


def test_decide_allow_then_call_permitted(db):
    v = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="session", path=db, now=1100.0)
    assert r["ok"] is True
    assert mcp_approval.evaluate_call(SID, TOOL, path=db, now=1200.0)["verdict"] == "allow"


def test_decide_deny_keeps_denying(db):
    v = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "deny", path=db, now=1100.0)
    assert r["ok"] is True
    v2 = mcp_approval.evaluate_call(SID, TOOL, path=db, now=1200.0)
    assert v2["verdict"] == "deny"


def test_invalid_inputs_raise(db):
    with pytest.raises(ValueError):
        mcp_approval.set_policy(SID, TOOL, "maybe", "global", path=db)
    with pytest.raises(ValueError):
        mcp_approval.set_policy(SID, TOOL, "allow", "everywhere", path=db)
    with pytest.raises(ValueError):
        mcp_approval.decide_approval(1, "maybe", path=db)


def test_policy_persistence_roundtrip(db):
    mcp_approval.set_policy(SID, TOOL, "block", "project", path=db)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT decision FROM mcp_tool_policies WHERE server_id=? AND tool_name=? AND scope=?",
            (SID, TOOL, "project"),
        ).fetchone()
    assert row[0] == "block"
    assert mcp_approval.get_policies(SID, TOOL, path=db) == {"project": "block"}


# ---- mcp_client 门禁集成 ----


def _enable():
    return SimpleNamespace(mcp_approval_enabled=True)


def test_gate_blocks_when_flag_off(monkeypatch, tmp_path):
    # flag 关闭 → 门禁不拦截（走到 server 查找，server 不存在返回 not found）
    out = mcp_client._call_tool("no-such", "t", settings=SimpleNamespace(mcp_approval_enabled=False))
    assert out == {"ok": False, "error": "server not found or disabled"}


def test_gate_denies_by_default_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: tmp_path / "g.db")
    out = mcp_client._call_tool("srv1", "some_tool", settings=_enable())
    assert out["ok"] is False
    assert out["error"] == "approval_required"
    assert out["mcp_approval_required"]["request_id"] is not None


def test_gate_allows_with_allow_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: tmp_path / "g2.db")
    mcp_approval.set_policy("srv1", "some_tool", "allow", "global")
    out = mcp_client._call_tool("srv1", "some_tool", settings=_enable())
    # 放行后走到 server 查找
    assert out == {"ok": False, "error": "server not found or disabled"}


def test_gate_fail_closed_on_approval_crash(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(mcp_approval, "evaluate_call", boom)
    out = mcp_client._call_tool("srv1", "some_tool", settings=_enable())
    assert out["ok"] is False
    assert out["error"] == "mcp_approval_unavailable"
    assert "mcp_approval_required" in out
