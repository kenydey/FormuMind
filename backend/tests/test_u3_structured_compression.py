"""U-3: 结构化问答路径同样做 query-aware 证据压缩。

generate_structured_answer 此前直接把 sources[:12] 的 snippet 拼进 prompt，
不经过压缩器。回归要求：
1. query_compress_enabled=True 时，sources 先过 compress_evidence；
2. identifier/title 不动（evidence_ref 校验不受影响）；
3. query_compress_enabled=False 时直通旧行为；
4. env_flags 注册表含三个 query_compress_* 条目。
"""

from __future__ import annotations

from app.domain.chat_schemas import ChatTurn, StructuredAnswer, StructuredAnswerResponse
from app.domain.schemas import Evidence
from app.services import chat_structured


def _ev(i: int) -> Evidence:
    return Evidence(
        source="local",
        identifier=f"kb:s{i}#c0",
        title=f"标题{i}",
        snippet="很长的证据文本 " * 200,
        relevance=0.9 - i * 0.01,
    )


def _fake_complete(*a, **k):
    fake = StructuredAnswerResponse(
        answer=StructuredAnswer(
            summary="摘要",
            formulation_hints=[
                {
                    "ingredient": "x",
                    "role": "r",
                    "typical_range": "1-2",
                    "evidence_ref": "kb:s0#c0",
                }
            ],
        )
    )
    return fake, None


class _Settings:
    chat_structured_enabled = True
    query_compress_enabled = True
    query_compress_llm_enabled = True
    query_compress_token_budget = 3000


def _run(monkeypatch, sources, settings):
    monkeypatch.setattr(
        "app.services.chat_structured.complete_structured", _fake_complete
    )
    return chat_structured.generate_structured_answer(
        "磷酸锌加多少", sources, history=None, settings=settings
    )


def test_structured_compresses_evidence_when_enabled(monkeypatch) -> None:
    sources = [_ev(i) for i in range(12)]
    seen: dict = {}

    def fake_compress(question, srcs, *, token_budget):
        seen["n_in"] = len(srcs)
        seen["budget"] = token_budget
        # 只保留前 3 条，模拟压缩裁剪
        return list(srcs[:3])

    monkeypatch.setattr(
        "app.services.query_aware_compression.compress_evidence", fake_compress
    )
    out, err = _run(monkeypatch, sources, _Settings())
    assert err is None and out is not None
    assert seen["n_in"] == 12
    assert seen["budget"] == 3000
    # identifier 保留 → evidence_ref 校验通过
    assert out.formulation_hints[0].evidence_ref == "kb:s0#c0"


def test_structured_passthrough_when_disabled(monkeypatch) -> None:
    sources = [_ev(i) for i in range(5)]
    called = {"n": 0}

    def fake_compress(question, srcs, *, token_budget):
        called["n"] += 1
        return srcs

    monkeypatch.setattr(
        "app.services.query_aware_compression.compress_evidence", fake_compress
    )

    class Off(_Settings):
        query_compress_enabled = False

    out, err = _run(monkeypatch, sources, Off())
    assert err is None and out is not None
    assert called["n"] == 0


def test_structured_compression_fail_open(monkeypatch) -> None:
    """压缩器抛错时回退原 sources，不影响结构化回答。"""
    sources = [_ev(i) for i in range(4)]

    def boom(question, srcs, *, token_budget):
        raise RuntimeError("compressor down")

    monkeypatch.setattr(
        "app.services.query_aware_compression.compress_evidence", boom
    )
    out, err = _run(monkeypatch, sources, _Settings())
    assert err is None and out is not None
    assert out.summary == "摘要"


def test_env_flags_registry_has_query_compress_entries() -> None:
    from app.services import env_flags

    flag_attrs = {f.attr for f in env_flags.FLAG_REGISTRY}
    assert "query_compress_enabled" in flag_attrs
    assert "query_compress_llm_enabled" in flag_attrs
    env_attrs = {v["attr"] for v in env_flags.ENV_VARS}
    assert "query_compress_token_budget" in env_attrs
    listed = {v["attr"] for v in env_flags.list_env_vars()}
    assert "query_compress_token_budget" in listed
