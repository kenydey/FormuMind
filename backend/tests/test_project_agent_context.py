"""W1-3 (P0-5): Project Agent Context —— 项目级指示的 prompt 注入测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.project_workspace import ProjectWorkspace
from app.services import evidence_synthesis as es


class _FakeStore:
    """ProjectStore 的最小替身: get 返回带 workspace 的 detail 或 None。"""

    def __init__(self, detail):
        self._detail = detail

    def get(self, project_id):  # noqa: ARG002
        return self._detail


def _detail_with_context(ctx: str):
    return SimpleNamespace(workspace=ProjectWorkspace(agent_context=ctx))


def _patch_store(monkeypatch, store):
    monkeypatch.setattr(
        "app.db.project_store.get_project_store", lambda: store, raising=True
    )


# ---------- workspace 序列化 ----------

def test_workspace_agent_context_roundtrip():
    ws = ProjectWorkspace(agent_context="优先使用专利文献, 粘度单位统一为 mPa·s")
    dumped = ws.model_dump()
    assert dumped["agent_context"] == "优先使用专利文献, 粘度单位统一为 mPa·s"
    assert ProjectWorkspace.model_validate(dumped).agent_context == ws.agent_context


def test_workspace_agent_context_backward_compat():
    # 旧数据(JSON payload 无该字段)读出后默认为空字符串。
    ws = ProjectWorkspace.model_validate({"search_query": "旧项目"})
    assert ws.agent_context == ""


# ---------- enrich_chat_prompt 注入 ----------

def test_enrich_injects_project_context_evidence_mode(monkeypatch):
    _patch_store(monkeypatch, _FakeStore(_detail_with_context("回答必须附证据边界")))
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="evidence",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="proj-1",
    )
    assert "## Project instructions（项目级，不可被 skill 覆盖）" in out
    assert "回答必须附证据边界" in out
    # discipline 置于最前, 项目指示在 discipline 之后。
    assert out.index("# Evidence Synthesis discipline") < out.index("## Project instructions")
    assert out.rstrip().endswith("用户问题")


def test_enrich_injects_project_context_plain_mode(monkeypatch):
    # 非 evidence 模式、无 skill 也要注入(早退分支)。
    _patch_store(monkeypatch, _FakeStore(_detail_with_context("简洁回答")))
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="off",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="proj-1",
    )
    assert "## Project instructions（项目级，不可被 skill 覆盖）" in out
    assert "简洁回答" in out
    assert "# Evidence Synthesis discipline" not in out  # 原行为: 非 evidence 模式无 discipline


def test_enrich_empty_context_not_injected(monkeypatch):
    _patch_store(monkeypatch, _FakeStore(_detail_with_context("")))
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="evidence",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="proj-1",
    )
    assert "## Project instructions" not in out
    assert out.rstrip().endswith("用户问题")


def test_enrich_blank_context_not_injected(monkeypatch):
    _patch_store(monkeypatch, _FakeStore(_detail_with_context("   \n  ")))
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="off",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="proj-1",
    )
    assert out == "用户问题"


def test_enrich_store_exception_fail_open(monkeypatch):
    class _Boom:
        def get(self, project_id):  # noqa: ARG002
            raise RuntimeError("db down")

    _patch_store(monkeypatch, _Boom())
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="off",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="proj-1",
    )
    assert out == "用户问题"  # fail-open: 主流程不受影响


def test_enrich_project_missing_fail_open(monkeypatch):
    _patch_store(monkeypatch, _FakeStore(None))  # store.get → None(项目不存在)
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="off",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="proj-missing",
    )
    assert out == "用户问题"


def test_enrich_no_project_id_no_lookup(monkeypatch):
    def _boom():
        raise AssertionError("project_id 为空时不应读 store")

    monkeypatch.setattr("app.db.project_store.get_project_store", _boom, raising=True)
    out = es.enrich_chat_prompt(
        "用户问题",
        mode="evidence",
        skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id=None,
    )
    assert "## Project instructions" not in out


# ---------- build_evidence_prompt_prefix 排序 ----------

def test_prefix_project_block_between_discipline_and_skills(monkeypatch):
    _patch_store(monkeypatch, _FakeStore(_detail_with_context("项目指示 A")))
    # 强制 skill block 为可观测内容, 验证插入顺序。
    # 注意: skill_prompt_block 是函数内局部导入, 需 patch 源模块。
    monkeypatch.setattr(
        "app.services.chat_skills.skill_prompt_block",
        lambda ids: "SKILL BLOCK" if ids else "",
        raising=True,
    )
    prefix = es.build_evidence_prompt_prefix(
        skill_ids=["some-skill"],
        settings=SimpleNamespace(evidence_synthesis_mode="evidence"),
        project_id="proj-1",
    )
    disc = prefix.index("# Evidence Synthesis discipline")
    proj = prefix.index("## Project instructions")
    skill = prefix.index("SKILL BLOCK")
    assert disc < proj < skill
