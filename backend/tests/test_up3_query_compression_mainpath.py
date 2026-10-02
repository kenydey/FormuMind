"""Up-3 回归：主路径 query 压缩接入后，引用顺序仍与 prompt 对齐。"""
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
        snippet=f"{title} 的摘要内容，用于压缩测试。",
        relevance=0.9,
    )


def _req():
    return SimpleNamespace(
        question="环氧底漆配方？",
        domain=None,
        history=[],
        sources=[],
        include_entity_resolution=False,
        selected_connectors=[],
        structure=None,
        mode="chat",
        project_id=None,
        clarified_entities=None,
        selected_skills=[],
        selected_mcp_servers=[],
    )


def _settings():
    return SimpleNamespace(
        chat_rerank_candidates=20,
        chat_rerank_top_k=20,
        chat_skills_runtime_enabled=False,
        query_compress_enabled=True,
        chat_context_max_chars=12000,
    )


class _FakeStore:
    def __init__(self, reranked):
        self._reranked = reranked

    def ingest(self, sources):
        pass

    def query(self, retrieval_query, k=0):
        return list(self._reranked)[:k] if k else list(self._reranked)


def _base_mocks(monkeypatch):
    srcs = [_ev("kb:A", "文献A"), _ev("kb:B", "文献B"), _ev("kb:C", "文献C")]
    reranked = [srcs[2], srcs[0], srcs[1]]
    monkeypatch.setattr(
        chat_api, "_augment_with_kb", lambda q, s, **kw: (list(s) + list(srcs), len(srcs), None, None)
    )
    import app.services.rag as rag

    monkeypatch.setattr(rag, "build_store", lambda: _FakeStore(reranked))
    import app.services.chat_context as chat_context

    monkeypatch.setattr(chat_context, "rewrite_query", lambda q, h, ce=None, **kw: (q, ""))
    monkeypatch.setattr(chat_context, "trim_history", lambda h, **kw: [])
    import app.services.chat_clarify as chat_clarify

    monkeypatch.setattr(chat_clarify, "detect_clarification", lambda *a, **kw: None)
    import app.services.connectors_builtin as connectors_builtin

    monkeypatch.setattr(connectors_builtin, "gather_connector_evidence", lambda *a, **kw: [])
    import app.services.text2sql as text2sql

    monkeypatch.setattr(text2sql, "hybrid_answer", lambda *a, **kw: {})
    return srcs


def test_stream_compression_reorders_plan_relevant(monkeypatch):
    # 压缩把顺序改成 B,C,A → plan["relevant"]（done citations 源）必须同步。
    srcs = _base_mocks(monkeypatch)

    monkeypatch.setattr(
        chat_api, "compress_evidence", lambda q, s, token_budget=3000: [srcs[1], srcs[2], srcs[0]]
    )
    # chat.py 用模块级 import：打补丁需作用到 chat_api 命名空间。
    plan = chat_api._stream_answer_plan(_req(), _settings())
    assert [e.identifier for e in plan["relevant"]] == ["kb:B", "kb:C", "kb:A"]


def test_stream_compression_fail_open(monkeypatch):
    _base_mocks(monkeypatch)

    def _boom(q, s, token_budget=3000):
        raise RuntimeError("compress down")

    monkeypatch.setattr(chat_api, "compress_evidence", _boom)
    plan = chat_api._stream_answer_plan(_req(), _settings())
    # fail-open：保持压缩前的 BM25 顺序
    assert [e.identifier for e in plan["relevant"]] == ["kb:C", "kb:A", "kb:B"]


def test_answer_question_compression_keeps_citation_order(monkeypatch):
    import app.services.llm as llm_mod

    srcs = [_ev("kb:A", "文献A"), _ev("kb:B", "文献B")]

    monkeypatch.setattr(llm_mod, "compress_evidence", lambda q, s, token_budget=3000: [srcs[1], srcs[0]])
    monkeypatch.setattr(llm_mod, "_call_llm", lambda prompt, **kw: "答案[^1][^2]")
    monkeypatch.setattr(llm_mod, "_paperqa_available", lambda: False)
    monkeypatch.setattr(
        "app.services.llm.get_settings",
        lambda: SimpleNamespace(
            query_compress_enabled=True,
            query_compress_token_budget=3000,
            chat_context_max_chars=12000,
            chat_rerank_candidates=20,
            chat_rerank_top_k=20,
            chat_cross_encoder_enabled=False,
        ),
    )
    answer, relevant = llm_mod.answer_question("问题？", list(srcs))
    assert answer == "答案[^1][^2]"
    # 返回的 relevant 与 prompt 同序（压缩后）
    assert [e.identifier for e in relevant] == ["kb:B", "kb:A"]


def test_stream_compression_uses_token_budget_not_char_budget(monkeypatch):
    # F-1 回归：budget 必须是 token 单位的 query_compress_token_budget，
    # 不能把 chat_context_max_chars（字符）直接当 token 传。
    srcs = _base_mocks(monkeypatch)

    seen = {}

    def _capture(q, s, token_budget=None):
        seen["token_budget"] = token_budget
        return list(s)

    monkeypatch.setattr(chat_api, "compress_evidence", _capture)
    settings = _settings()
    settings.query_compress_token_budget = 500
    settings.chat_context_max_chars = 12000
    chat_api._stream_answer_plan(_req(), settings)
    assert seen["token_budget"] == 500


def test_compress_budget_actually_fits_tokens():
    # F-1 回归：默认预算（3000 tokens ≈ 12000 字符）下，超预算 evidence
    # 经 compress_evidence 后估计 token 数必须 ≤ budget（预算保证真实生效）。
    import app.services.query_aware_compression as qc

    evs = [
        _ev(f"kb:{i}", f"文献{i}标题")
        for i in range(30)
    ]
    for e in evs:
        e.snippet = "化学内容。" * 500  # 每条约 2500 字符
    out = qc.compress_evidence("环氧底漆配方？", evs, token_budget=3000)
    total = sum(qc.estimate_tokens(e.snippet) for e in out)
    assert total <= 3000, f"compressed {total} tokens > budget 3000"
