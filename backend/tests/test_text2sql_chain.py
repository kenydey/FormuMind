"""C-3 chain integration: hybrid_answer as the unified chat-chain hook.

Covers: routing gate, structured path with deterministic rows, hybrid route,
fail-open on SQL errors and guardrail rejections, unstructured regression
(chain behavior identical when the question carries no structured signal),
single literature retrieval (evidence= passed in, never re-retrieved), and
the data_sources provenance marker on ChatResponse.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.domain.chat_schemas import ChatResponse
from app.services import text2sql
from app.services.text2sql import hybrid_answer


def _mem_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE measurements (id INTEGER PRIMARY KEY, "
                "experiment_id INTEGER, metric TEXT, value REAL, unit TEXT, "
                "test_method TEXT, created_at TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO measurements (experiment_id, metric, value, unit) "
                "VALUES (1, '耐蚀性', 96.0, 'h'), (2, '耐蚀性', 48.0, 'h')"
            )
        )
    return engine


def _settings(**overrides):
    s = get_settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _chain(question, **kw):
    """Call hybrid_answer the way app/api/chat.py does."""
    kw.setdefault("evidence", [])
    kw.setdefault("include_evidence_text", False)
    return hybrid_answer(question, **kw)


def test_routing_disabled_returns_empty_block():
    out = _chain(
        "查询耐蚀性大于72h的实验",
        settings=_settings(text2sql_routing_enabled=False),
        engine=_mem_engine(),
        complete_fn=lambda sys, usr: "SELECT 1",
    )
    assert out["fused_context"] == ""
    assert out["route"] == "disabled"
    assert out["data_sources"] == ["kb_evidence"]


def test_unstructured_question_leaves_chain_untouched():
    calls = []

    def _complete(sys, usr):
        calls.append((sys, usr))
        return "SELECT 1"

    out = _chain(
        "磷化膜的成膜机理是什么",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=_complete,
    )
    # No structured signal -> no LLM call, no SQL, chain prompt identical.
    assert out["fused_context"] == ""
    assert calls == []
    assert out["route"] == "unstructured"
    assert out["data_sources"] == ["kb_evidence"]


def test_structured_path_returns_deterministic_rows():
    def _complete(sys, usr):
        return (
            "SELECT experiment_id, metric, value, unit FROM measurements "
            "WHERE metric = '耐蚀性' AND value > 72"
        )

    out = _chain(
        "查询耐蚀性大于72小时的实验记录",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=_complete,
    )
    assert out["route"] == "structured"
    assert out["row_count"] == 1
    assert out["data_sources"] == ["structured_sql", "kb_evidence"]
    assert "确定性数据结果" in out["fused_context"]
    assert "96.0" in out["fused_context"]
    assert "48.0" not in out["fused_context"]  # filtered by the WHERE clause


def test_hybrid_route_runs_sql_and_frames_fusion():
    def _complete(sys, usr):
        return "SELECT experiment_id, value FROM measurements WHERE value > 72"

    out = _chain(
        "查询耐蚀性大于72h的实验数据，并介绍提升耐蚀性的方法原理",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=_complete,
    )
    assert out["route"] == "hybrid"
    assert out["row_count"] == 1
    assert "明确区分" in out["fused_context"]
    assert "96.0" in out["fused_context"]


def test_structured_path_no_matching_rows_still_marks_source():
    def _complete(sys, usr):
        return "SELECT experiment_id FROM measurements WHERE value > 10000"

    out = _chain(
        "查询耐蚀性大于10000小时的实验",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=_complete,
    )
    assert out["route"] == "structured"
    assert out["row_count"] == 0
    assert "无匹配数据" in out["fused_context"]
    assert out["data_sources"] == ["structured_sql", "kb_evidence"]


def test_fail_open_on_guardrail_rejection():
    # DROP must be rejected by validate_select_only -> fail-open, no raise.
    out = _chain(
        "查询耐蚀性大于72小时的实验并删除它们",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=lambda sys, usr: "DROP TABLE measurements",
    )
    assert out["fused_context"] == ""
    assert out["route"] == "fallback"
    assert out["data_sources"] == ["kb_evidence"]


def test_fail_open_on_llm_error():
    def _boom(sys, usr):
        raise RuntimeError("llm down")

    out = _chain(
        "统计耐蚀性大于72h的实验数量",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=_boom,
    )
    assert out["fused_context"] == ""
    assert out["route"] == "fallback"


def test_fail_open_on_none_sql():
    out = _chain(
        "查询耐蚀性大于72h的实验",
        settings=_settings(text2sql_routing_enabled=True),
        engine=_mem_engine(),
        complete_fn=lambda sys, usr: None,
    )
    assert out["fused_context"] == ""
    # Generation failure -> honest fallback, not "no matching data".
    assert out["route"] == "fallback"


def test_evidence_passed_in_is_not_reretrieved():
    calls = []

    def _retrieve(*a, **k):
        calls.append(1)
        return [{"text": "should not be called"}]

    out = hybrid_answer(
        "查询耐蚀性大于72h的实验",
        _mem_engine(),
        settings=_settings(text2sql_routing_enabled=True),
        complete_fn=lambda sys, usr: "SELECT 1",
        retrieve_fn=_retrieve,
        evidence=[{"text": "pre-retrieved"}],
        include_evidence_text=True,
    )
    # C-3: chat passes its own sources; hybrid_answer must not re-retrieve.
    assert calls == []
    assert out["evidence_count"] == 1
    assert "pre-retrieved" in out["fused_context"]


def test_enforce_limit_applied_server_side():
    seen = {}

    def _complete(sys, usr):
        return "SELECT experiment_id FROM measurements"  # no LIMIT

    orig_execute = text2sql.execute_sql

    def _spy(engine, sql, **kw):
        seen["sql"] = sql
        return orig_execute(engine, sql, **kw)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(text2sql, "execute_sql", _spy)
    try:
        _chain(
            "查询所有耐蚀性实验记录",
            settings=_settings(text2sql_routing_enabled=True),
            engine=_mem_engine(),
            complete_fn=_complete,
        )
    finally:
        monkey.undo()
    assert "LIMIT" in seen["sql"].upper()


def test_chat_response_carries_data_sources():
    resp = ChatResponse(
        answer="x",
        citations=[],
        data_sources=["structured_sql", "kb_evidence"],
    )
    assert resp.data_sources == ["structured_sql", "kb_evidence"]
    # Default stays None for callers that do not set it.
    resp2 = ChatResponse(answer="x", citations=[])
    assert resp2.data_sources is None
    assert isinstance(resp.citations, list)


def test_hook_never_raises_on_broken_engine():
    out = _chain(
        "查询耐蚀性大于72h的实验",
        settings=_settings(text2sql_routing_enabled=True),
        engine=None,  # falls back to default_session_factory().bind
        complete_fn=lambda sys, usr: "SELECT 1",
    )
    # Must not raise regardless of environment DB state.
    assert isinstance(out["fused_context"], str)
    assert isinstance(out, dict)
