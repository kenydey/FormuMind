"""B-Q1 回归：stream 的 citations 必须与 prompt 的 [^n] 同序。

_stream_answer_plan 用 BM25 重排后的 relevant 建 prompt（[^1] = relevant[0]），
而 stream done 曾取重排前的 plan["sources"][:8] 作 citations，导致 [^n] 与
前端引用卡片错位。修复后 plan 透出 "relevant"，done 取 relevant[:8]。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import chat as chat_api
from app.domain.schemas import Evidence


def _ev(ident, title):
    return Evidence(
        source="kb",
        identifier=ident,
        title=title,
        snippet=f"{title} 的摘要内容，用于排序测试。",
        relevance=0.9,
    )


class _FakeStore:
    """ingest 后 query 返回固定重排顺序，模拟 BM25 重排。"""

    def __init__(self, reranked):
        self._reranked = reranked
        self.ingested = None

    def ingest(self, sources):
        self.ingested = list(sources)

    def query(self, retrieval_query, k=0):
        return list(self._reranked)[:k] if k else list(self._reranked)


def _req():
    return SimpleNamespace(
        question="水性环氧底漆的盐雾性能如何？",
        history=[],
        clarified_entities=None,
        sources=[],
        project_id=None,
        include_entity_resolution=False,
        selected_connectors=[],
        selected_skills=[],
        selected_mcp_servers=[],
        mode="chat",
        structure=None,
        domain="coating",
    )


def _settings():
    return SimpleNamespace(
        chat_rerank_candidates=20,
        chat_rerank_top_k=20,
        chat_skills_runtime_enabled=False,
    )


def test_stream_plan_relevant_matches_prompt_order(monkeypatch):
    srcs = [_ev("kb:A", "文献A"), _ev("kb:B", "文献B"), _ev("kb:C", "文献C")]
    reranked = [srcs[2], srcs[0], srcs[1]]  # BM25 重排：C,A,B

    monkeypatch.setattr(
        chat_api,
        "_augment_with_kb",
        lambda q, s, **kw: (list(s) + list(srcs), len(srcs), None, None),
    )
    import app.services.rag as rag

    monkeypatch.setattr(rag, "build_store", lambda: _FakeStore(reranked))
    import app.services.chat_context as chat_context

    monkeypatch.setattr(
        chat_context, "rewrite_query", lambda q, h, ce=None, **kw: (q, "")
    )
    monkeypatch.setattr(chat_context, "trim_history", lambda h, **kw: [])
    import app.services.chat_clarify as chat_clarify

    monkeypatch.setattr(
        chat_clarify, "detect_clarification", lambda *a, **kw: None
    )
    import app.services.connectors_builtin as connectors_builtin

    monkeypatch.setattr(
        connectors_builtin, "gather_connector_evidence", lambda *a, **kw: []
    )
    import app.services.text2sql as text2sql

    monkeypatch.setattr(text2sql, "hybrid_answer", lambda *a, **kw: {})

    plan = chat_api._stream_answer_plan(_req(), _settings())

    # prompt 的 [^n] 按 relevant（重排后）编号
    assert [e.identifier for e in plan["relevant"]] == ["kb:C", "kb:A", "kb:B"]
    # done 负载取的 relevant[:8] 与 prompt 同序
    cites = plan["relevant"][: min(8, len(plan["relevant"]))]
    assert [e.identifier for e in cites] == ["kb:C", "kb:A", "kb:B"]
    # prompt 内证据行 [^1] 对应 C，[^2] 对应 A（行首编号，非指令文本中的提及）
    prompt = plan["prompt"]
    ev_lines = [
        ln for ln in prompt.splitlines() if ln.startswith("[^") and "]" in ln
    ]
    assert ev_lines, "prompt 中应包含证据行"
    assert ev_lines[0].startswith("[^1]") and "文献C" in ev_lines[0]
    assert ev_lines[1].startswith("[^2]") and "文献A" in ev_lines[1]
    # sources 保持原始检索序（meta source_count 用）
    assert [e.identifier for e in plan["sources"]][:3] == ["kb:A", "kb:B", "kb:C"]


def test_stream_plan_empty_sources_no_crash(monkeypatch):
    monkeypatch.setattr(
        chat_api, "_augment_with_kb", lambda q, s, **kw: ([], 0, None, None)
    )
    import app.services.rag as rag

    monkeypatch.setattr(rag, "build_store", lambda: _FakeStore([]))
    import app.services.chat_context as chat_context

    monkeypatch.setattr(
        chat_context, "rewrite_query", lambda q, h, ce=None, **kw: (q, "")
    )
    monkeypatch.setattr(chat_context, "trim_history", lambda h, **kw: [])
    import app.services.chat_clarify as chat_clarify

    monkeypatch.setattr(
        chat_clarify, "detect_clarification", lambda *a, **kw: None
    )
    import app.services.connectors_builtin as connectors_builtin

    monkeypatch.setattr(
        connectors_builtin, "gather_connector_evidence", lambda *a, **kw: []
    )
    import app.services.text2sql as text2sql

    monkeypatch.setattr(text2sql, "hybrid_answer", lambda *a, **kw: {})

    plan = chat_api._stream_answer_plan(_req(), _settings())
    assert plan["relevant"] == []
    assert plan["relevant"][:8] == []
