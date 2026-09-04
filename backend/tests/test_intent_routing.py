"""R3: entity_resolver 意图路由测试(2026-09-04)。"""
from __future__ import annotations

import json

import pytest

from app.services.kg.entity_resolver import (
    _ENUMERATIVE_RE,
    _llm_intent_mode,
    detect_mode,
)


# ── 正则快层(零 LLM 开销) ────────────────────────────────────────────────


def test_regex_covers_review_inventory_phrasing():
    """审查原例 + 常见盘点问法 → enumerative(不触发 LLM)。"""
    for q in (
        "盘点一下近年来关于无铬钝化的综述",
        "汇总一下水性环氧的文献",
        "有哪些牌号可以做无铬钝化",
        "筛出所有耐盐雾超过500小时的材料",
    ):
        assert detect_mode(q) == "enumerative", q


def test_regex_not_fire_on_semantic_analysis():
    """高歧义分析问法不进 enumerative(留给 LLM 层, 正则不误伤)。"""
    for q in (
        "总结一下这个配方的耐盐雾性能",
        "帮我整理一下这批实验数据",
        "对比环氧和聚氨酯的耐蚀差异",
    ):
        assert detect_mode(q) == "auto", q


# ── LLM 结构化兜底(3s 超时) ──────────────────────────────────────────────


def test_llm_intent_mode_parses(monkeypatch):
    def fake_call_llm(prompt):
        return json.dumps({"mode": "enumerative"}, ensure_ascii=False)

    monkeypatch.setattr("app.services.llm._call_llm", fake_call_llm)
    assert _llm_intent_mode("这个领域近三年的研究进展综述有哪些值得看") == "enumerative"


def test_llm_intent_mode_fence_stripped(monkeypatch):
    def fake_call_llm(prompt):
        return "```json\n{\"mode\": \"hybrid\"}\n```"

    monkeypatch.setattr("app.services.llm._call_llm", fake_call_llm)
    assert _llm_intent_mode("含 zinc phosphate 的体系怎么提升耐盐雾同时降成本") == "hybrid"


def test_llm_intent_mode_timeout_returns_none(monkeypatch):
    """LLM 挂起 → 3s deadline 返回 None(不无限等, 调用方保持现状)。"""
    import time

    def slow_call_llm(prompt):
        time.sleep(30)

    monkeypatch.setattr("app.services.llm._call_llm", slow_call_llm)
    from app.services import llm as _llm_mod

    calls = []

    def deadline(fn, seconds):
        import concurrent.futures
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return ex.submit(fn).result(timeout=seconds)
        except Exception:
            return None
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    monkeypatch.setattr(_llm_mod, "_call_with_deadline", deadline)
    t0 = time.time()
    assert _llm_intent_mode("盘点近三年自沉积涂料耐蚀改性综述有哪些方向") is None
    assert time.time() - t0 < 6  # 3s deadline 内返回


def test_llm_intent_mode_bad_json_and_short_query(monkeypatch):
    def fake_call_llm(prompt):
        return "not json at all"

    monkeypatch.setattr("app.services.llm._call_llm", fake_call_llm)
    assert _llm_intent_mode("盘点近三年自沉积涂料耐蚀改性综述有哪些方向") is None
    # 短查询不触发 LLM
    assert _llm_intent_mode("有哪些") is None
    assert _llm_intent_mode("") is None
