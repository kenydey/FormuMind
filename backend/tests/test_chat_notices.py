"""B-1: fail-open 降级显式提示位。

numeric check 跳过、BM25-only 退化时，响应必须携带可见提示位（notices），
不再静默。覆盖：
1. numeric check 抛异常 → notices 含 numeric_check_skipped，答案不受影响；
2. 正常路径 → notices 为空；
3. kb_used>0 且向量层退化 → retrieval_degraded 提示；
4. kb_used=0 或向量层正常 → 无提示；
5. ChatResponse 接受 notices 字段。
"""
import pytest

from app.api.chat import _apply_answer_gates, _retrieval_degradation_notices
from app.config import get_settings
from app.domain.chat_schemas import ChatResponse
from app.domain.schemas import Evidence


def _ev(title="t", snippet="s"):
    return Evidence(source="kb", identifier="k1", title=title, snippet=snippet, relevance=0.9)


def test_numeric_check_skipped_emits_notice(monkeypatch):
    import app.services.numeric_check as nc

    def _boom(answer, evidence_texts):
        raise RuntimeError("boom")

    monkeypatch.setattr(nc, "check_answer_numbers", _boom)
    gated, _, abstained, notices = _apply_answer_gates(
        "q", "某答案 42", [_ev()], [], None, get_settings()
    )
    assert gated == "某答案 42"  # fail-open：答案不受影响
    assert abstained is False
    codes = [n["code"] for n in notices]
    assert "numeric_check_skipped" in codes
    msg = next(n["message"] for n in notices if n["code"] == "numeric_check_skipped")
    assert "数值一致性" in msg


def test_numeric_check_disabled_no_notice():
    s = get_settings()
    s.chat_numeric_check_enabled = False
    _, _, _, notices = _apply_answer_gates("q", "答案", [_ev()], [], None, s)
    assert notices == []


def test_retrieval_degraded_notice(monkeypatch):
    import app.services.kb_index as kbi

    monkeypatch.setattr(
        kbi,
        "kb_vector_notice",
        lambda: {"vector_mode": "degraded", "vector_hint": "已退化"},
    )
    notices = _retrieval_degradation_notices(3)
    assert len(notices) == 1
    assert notices[0]["code"] == "retrieval_degraded"
    assert notices[0]["message"] == "已退化"


def test_retrieval_notice_absent_when_unused_or_healthy(monkeypatch):
    import app.services.kb_index as kbi

    calls = []
    monkeypatch.setattr(
        kbi, "kb_vector_notice", lambda: calls.append(1) or {"vector_mode": "degraded"}
    )
    # kb 未参与检索 → 不探测、不提示
    assert _retrieval_degradation_notices(0) == []
    assert calls == []
    # 向量层正常 → 无提示
    monkeypatch.setattr(
        kbi, "kb_vector_notice", lambda: {"vector_mode": "semantic", "vector_hint": ""}
    )
    assert _retrieval_degradation_notices(5) == []
    # 探测失败 → fail-open，不阻断
    monkeypatch.setattr(kbi, "kb_vector_notice", lambda: None)
    assert _retrieval_degradation_notices(5) == []


def test_chat_response_accepts_notices():
    r = ChatResponse(
        answer="a",
        citations=[],
        notices=[{"code": "x", "message": "y"}],
    )
    assert r.notices == [{"code": "x", "message": "y"}]
    r2 = ChatResponse(answer="a", citations=[])
    assert r2.notices is None
