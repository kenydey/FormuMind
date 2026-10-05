"""Round 3 Wave 1 — agentic iterative retrieval loop.

A/B 结论（2026-09-28，32 组 golden_rigor 合成语料，去重后）：
  single_cov=0.188，agent_cov=0.188，第二轮触发仅 2/32，调用 32→34。
根因：短中文化学问题经 extract_facets 只剩 1~2 个分面，单发检索已 trivially
覆盖 → 循环以 reason="covered" 在第 1 轮停止，启发式缺口信号太弱。
决策矩阵 → 提升不显著，保持默认关闭（agent_search_enabled=False），
循环作为 opt-in 能力保留（FORMUMIND_AGENT_SEARCH_ENABLED=true）。
"""
from __future__ import annotations

import time
import types

from app.domain.schemas import Evidence
from app.services.agent_search_loop import (
    AgentSearchResult,
    agent_search,
    agent_search_enabled,
    assess_gap,
    extract_facets,
    rewrite_query,
)


def _ev(identifier: str, title: str = "", snippet: str = "") -> Evidence:
    return Evidence(source="t", identifier=identifier, title=title or identifier,
                   snippet=snippet, relevance=0.5)


# ── facet 提取 ──────────────────────────────────────────────

def test_extract_facets_chinese():
    facets = extract_facets("盐雾试验如何评价防腐涂料？")
    assert "盐雾" in facets
    assert all(len(f) >= 2 for f in facets)
    # 停用词/单字被过滤
    assert "的" not in facets and "如何" not in facets


def test_extract_facets_dedup_and_cap():
    facets = extract_facets("盐雾 盐雾 附着力 腐蚀 腐蚀", max_facets=3)
    assert facets == facets[:3]
    assert len(set(facets)) == len(facets)


def test_extract_facets_empty():
    assert extract_facets("") == []
    assert extract_facets("的了是在") == []


# ── 缺口评估 / 改写 ─────────────────────────────────────────

def test_assess_gap_uncovered_then_covered():
    evs = [_ev("d1", snippet="盐雾试验 1000 小时")]
    assert assess_gap(["盐雾", "附着力"], evs) == ["附着力"]
    evs.append(_ev("d2", snippet="附着力划格 0 级"))
    assert assess_gap(["盐雾", "附着力"], evs) == []


def test_assess_gap_empty_facets():
    assert assess_gap([], [_ev("d1")]) == []


def test_rewrite_query_appends_uncovered():
    q = rewrite_query("盐雾试验", ["附着力"])
    assert q == "盐雾试验 附着力"


def test_rewrite_query_idempotent():
    assert rewrite_query("盐雾 附着力", ["附着力"]) == "盐雾 附着力"
    assert rewrite_query("盐雾试验", []) == "盐雾试验"


# ── 循环行为 ────────────────────────────────────────────────

def test_agent_search_single_round_when_covered():
    calls = []

    def fake(q, k, **kw):
        calls.append(q)
        return [_ev("d1", snippet="盐雾 附着力都提到")]

    res = agent_search("盐雾 附着力", fake, k=6, max_iters=3)
    assert res.rounds == 1
    assert res.stopped_reason == "covered"
    assert [e.identifier for e in res.evidence] == ["d1"]
    assert len(calls) == 1


def test_agent_search_iterates_on_gap():
    calls = []

    def fake(q, k, **kw):
        calls.append(q)
        if len(calls) == 1:
            return [_ev("d1", snippet="盐雾试验内容")]
        return [_ev("d2", snippet="附着力划格内容")]

    res = agent_search("盐雾 附着力", fake, k=6, max_iters=3)
    assert res.rounds == 2
    assert res.stopped_reason == "covered"
    assert [e.identifier for e in res.evidence] == ["d1", "d2"]
    # 改写是幂等的（"附着力"已在原查询中），第二轮按调用次数推进
    assert len(calls) == 2


def test_agent_search_dedups_across_rounds():
    def fake(q, k, **kw):
        # 每轮都返回同一批（含重复 identifier）
        return [_ev("d1", snippet="盐雾"), _ev("d1", snippet="盐雾重复")]

    res = agent_search("盐雾 附着力", fake, k=6, max_iters=3)
    assert [e.identifier for e in res.evidence] == ["d1"]
    # 第二轮无新证据 → no_new 停止
    assert res.stopped_reason == "no_new"


def test_agent_search_max_iters_guard():
    calls = []

    def fake(q, k, **kw):
        calls.append(q)
        # 永远返回新文档且永远有缺口
        return [_ev("d%d" % len(calls), snippet="新文档内容")]

    res = agent_search("盐雾 附着力 耐蚀性 腐蚀机理", fake, k=6, max_iters=2)
    assert res.rounds == 2
    assert res.stopped_reason == "max_iters"
    assert len(calls) == 2


def test_agent_search_time_budget_stops():
    def fake(q, k, **kw):  # pragma: no cover
        raise AssertionError("must not be called")

    res = agent_search("盐雾试验", fake, k=6, max_iters=3, time_budget_s=0)
    assert res.rounds == 0
    assert res.evidence == []
    assert res.stopped_reason == "budget"


def test_a_zero_budget_runs_no_round_even_when_the_clock_has_not_moved(monkeypatch):
    """Windows' monotonic clock ticks every ~15.6 ms, so ``elapsed`` is exactly 0 — and ``0 > 0`` let one round run."""
    from app.services import agent_search_loop

    monkeypatch.setattr(agent_search_loop.time, "monotonic", lambda: 1234.5)

    def fake(q, k, **kw):  # pragma: no cover
        raise AssertionError("must not be called")

    res = agent_search("盐雾试验", fake, k=6, max_iters=3, time_budget_s=0)
    assert (res.rounds, res.stopped_reason, res.evidence) == (0, "budget", [])


def test_agent_search_fail_open_on_round_error():
    calls = []

    def fake(q, k, **kw):
        calls.append(q)
        if len(calls) == 1:
            return [_ev("d1", snippet="盐雾内容")]
        raise RuntimeError("boom")

    # 第一轮覆盖了 facet"盐雾"→ covered，不会走到第二轮；换个有缺口的 query
    res = agent_search("盐雾 附着力", fake, k=6, max_iters=3)
    assert res.rounds == 2
    assert res.stopped_reason == "error"
    # fail-open：保留已有证据
    assert [e.identifier for e in res.evidence] == ["d1"]


def test_agent_search_no_new_evidence_stops():
    calls = []

    def fake(q, k, **kw):
        calls.append(q)
        return [_ev("d1", snippet="盐雾内容")]  # 永远只有盐雾，附着力永无缺口补充

    res = agent_search("盐雾 附着力", fake, k=6, max_iters=3)
    assert res.rounds == 2
    assert res.stopped_reason == "no_new"


# ── 开关 ────────────────────────────────────────────────────

def test_agent_search_disabled_by_default():
    assert agent_search_enabled(types.SimpleNamespace()) is False
    assert agent_search_enabled(types.SimpleNamespace(agent_search_enabled=True)) is True


def test_retrieve_evidence_split_routes_to_loop(monkeypatch):
    """kb_index.retrieve_evidence 在开关开时走 agent_search 分流。"""
    import app.services.kb_index as kb_index
    import app.services.agent_search_loop as loop_mod

    monkeypatch.setattr(kb_index, "kb_enabled", lambda: True)
    seen_queries = []

    def fake_hybrid(q, k=4, **kw):
        seen_queries.append(q)
        return [_ev("h1", snippet="盐雾 附着力")]

    monkeypatch.setattr(kb_index, "search_chunks_hybrid", fake_hybrid)
    monkeypatch.setattr(loop_mod, "agent_search_enabled", lambda *a, **k: True)

    out = kb_index.retrieve_evidence("盐雾 附着力", k=6)
    assert [e.identifier for e in out] == ["h1"]
    assert seen_queries  # 确实走了检索


def test_retrieve_evidence_split_off_by_default(monkeypatch):
    """开关关闭时保持原单发链路（agent_search_loop.agent_search 未被调用）。"""
    import app.services.kb_index as kb_index
    import app.services.agent_search_loop as loop_mod

    monkeypatch.setattr(kb_index, "kb_enabled", lambda: True)

    def fake_hybrid(q, k=4, **kw):
        return [_ev("h1", snippet="x")]

    monkeypatch.setattr(kb_index, "search_chunks_hybrid", fake_hybrid)
    monkeypatch.setattr(loop_mod, "agent_search_enabled", lambda *a, **k: False)
    called = []
    monkeypatch.setattr(loop_mod, "agent_search",
                        lambda *a, **k: called.append(1) or AgentSearchResult([], 0))

    out = kb_index.retrieve_evidence("盐雾", k=6)
    assert [e.identifier for e in out] == ["h1"]
    assert called == []


# ── A/B 回归（结论锁定） ────────────────────────────────────

def _norm(t: str) -> str:
    import re
    t = (t or "").lower()
    return re.sub(r"[\s\u3000\-–—_.,;:!?，。；：！？、（）()\[\]【】\"'“”‘’·/\\]+", "", t)


def _build_corpus():
    from app.resources.golden_rigor import golden_rigor_pairs

    corpus, seen = [], set()
    for i, pair in enumerate(golden_rigor_pairs):
        for j, ev in enumerate(pair.get("evidence") or []):
            ident = ev.get("identifier") or "g%d-%d" % (i, j)
            if ident in seen:
                continue
            seen.add(ident)
            corpus.append(_ev(ident, title=ev.get("title") or "", snippet=ev.get("text") or ""))
    return corpus


def _single_shot_factory(corpus):
    from app.services.rag import _bm25_tokenize

    def single_shot(query, k, **kw):
        qtoks = {_norm(w) for w in _bm25_tokenize(query) if len(w.strip()) >= 2}
        scored = []
        for ev in corpus:
            dtoks = {_norm(w) for w in _bm25_tokenize(ev.title + " " + ev.snippet)
                     if len(w.strip()) >= 2}
            scored.append((len(qtoks & dtoks), ev))
        scored.sort(key=lambda x: -x[0])
        return [ev for s, ev in scored[:k] if s > 0]

    return single_shot


def _coverage(pair, retrieved) -> float:
    texts = [_norm(ev.title + " " + ev.snippet) for ev in retrieved]
    claims = pair.get("key_claims") or []
    if not claims:
        return 1.0
    hit = 0
    for c in claims:
        kws = c.get("keywords") or []
        m = sum(1 for kw in kws if any(_norm(kw) in t for t in texts))
        if kws and m / len(kws) >= 0.5:
            hit += 1
    return hit / len(claims)


def test_ab_golden_agent_does_not_regress_single_shot():
    """A/B 可复现 + 结论锁定：启发式循环在 golden 集上无提升 → 默认保持关闭。

    实测（2026-09-28）：single_cov == agent_cov（0.188），第二轮触发 2/32。
    若未来 heuristic/语料变化导致 agent 显著优于 single，可据此翻转默认开关。
    """
    from app.resources.golden_rigor import golden_rigor_pairs

    corpus = _build_corpus()
    single_shot = _single_shot_factory(corpus)
    tot_s = tot_a = 0.0
    for pair in golden_rigor_pairs:
        q = pair["question"]
        tot_s += _coverage(pair, single_shot(q, 6))
        res = agent_search(q, single_shot, k=6, max_iters=3)
        tot_a += _coverage(pair, res.evidence)
        # 循环永不返回比单发更差的证据集（只做追加式合并+去重）
        assert res.rounds >= 1
    n = len(golden_rigor_pairs)
    assert tot_a / n >= tot_s / n - 0.01
