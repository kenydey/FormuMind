"""Phase 2 单元测试：子块检索、行内引用、专利串联、agent 数值检查点。"""
from types import SimpleNamespace

import pytest

from app.domain.schemas import Evidence
from app.services.agent_search_loop import assess_gap_plus, extract_numeric_facets
from app.services.children_retrieval import (
    child_keyword_score,
    children_rerank,
    split_children,
    _tokens,
)
from app.services.hybrid_search import ScoredChunk


def _ev(ident, title="", snippet="", page=None, relevance=0.5):
    return Evidence(
        source="kb", identifier=ident, title=title, snippet=snippet,
        relevance=relevance, page=page,
    )


def _chunk(text, ord_no=0, source_id="s1"):
    return SimpleNamespace(
        id=f"c{ord_no}", source_id=source_id, ord=ord_no, text=text,
        heading_path="", page_no=3, paragraph_idx=None,
        offset_start=None, offset_end=None, meta={},
    )


def test_split_children_cjk():
    # 短句按 max_child_chars 合并为一个 child（设计如此：减少碎片）。
    parts = split_children("第一句。第二句！第三句？\n第四句。")
    assert parts == ["第一句。 第二句！ 第三句？ 第四句。"]
    parts = split_children("第一句。第二句。", max_child_chars=4)
    assert parts == ["第一句。", "第二句。"]


def test_split_children_long_slice():
    long = "表头|" + "x" * 900
    parts = split_children(long, max_child_chars=400)
    assert len(parts) >= 2
    assert "".join(parts).replace(" ", "") == long.replace(" ", "")


def test_child_keyword_score():
    qt = _tokens("除油剂配方 耐蚀性 72h")
    assert child_keyword_score(qt, "该除油剂配方耐蚀性达到72h") == 1.0
    assert child_keyword_score(qt, "今天天气不错") == 0.0
    assert child_keyword_score(set(), "任何") == 0.0


def test_children_rerank_promotes_precise_parent():
    # Distractor parent: many generic query-term hits spread across sentences,
    # but no sentence contains the full answer.
    distractor = _chunk(
        "除油剂配方研究进展。除油剂配方在工业中应用广泛。"
        "本文综述除油剂配方的历史。配方优化方向很多。"
    )
    # True parent: one sentence nails all query terms, buried in noise.
    true = _chunk(
        "无关引言内容很长很长。背景介绍段落文字。"
        "该除油剂配方耐蚀性达到72h，满足要求。后续展望内容。"
    )
    scored = [
        ScoredChunk(chunk=distractor, bm25_score=0.9, cosine_score=0.9, hybrid_score=0.9),
        ScoredChunk(chunk=true, bm25_score=0.2, cosine_score=0.2, hybrid_score=0.2),
    ]
    out = children_rerank("除油剂配方耐蚀性72h", scored, top_k=2)
    assert out[0].chunk is true  # child match promotes the true parent
    assert out[1].chunk is distractor


def test_children_rerank_never_demotes_without_child_signal():
    a = _chunk("完全无关的内容。")
    b = _chunk("也完全无关。")
    scored = [
        ScoredChunk(chunk=a, bm25_score=0.8, cosine_score=0.8, hybrid_score=0.8),
        ScoredChunk(chunk=b, bm25_score=0.3, cosine_score=0.3, hybrid_score=0.3),
    ]
    out = children_rerank("不存在的查询词xyz", scored, top_k=2)
    # No child signal → falls back to hybrid order (promotion is max(), never demote).
    assert [s.chunk for s in out] == [a, b]


def test_format_inline_citation_with_page():
    from app.services.kb_index import format_evidence_citations, format_inline_citation

    ev = _ev("kb:s#c0", title="某专利", page=5)
    assert format_inline_citation(ev, 1) == "[1] 某专利 · 第5页"
    ev2 = _ev("kb:s#c1", title="无页码文献", page=None)
    assert format_inline_citation(ev2, 2) == "[2] 无页码文献"
    block = format_evidence_citations([ev, ev2])
    assert block.splitlines() == ["[1] 某专利 · 第5页", "[2] 无页码文献"]


def test_format_inline_citation_uses_locator_not_new_infra():
    # CitationLocator 全空 → from_dict 返回 None；format 不应崩溃。
    from app.domain.citations import CitationLocator

    assert CitationLocator(page=None).to_dict() == {}
    assert CitationLocator.from_dict({}) is None


def test_patent_stitch_siblings(monkeypatch):
    import app.services.kb_index as ki

    rows = [
        SimpleNamespace(id="c0", source_id="p1", ord=0, text="权利要求1：一种除油剂配方",
                        heading_path="", page_no=1, paragraph_idx=None,
                        offset_start=None, offset_end=None,
                        meta={"patent_tags": {"claim_no": 1}}),
        SimpleNamespace(id="c1", source_id="p1", ord=1, text="实施例1：按权利要求1制备",
                        heading_path="", page_no=2, paragraph_idx=None,
                        offset_start=None, offset_end=None,
                        meta={"patent_tags": {"claim_no": 1, "example_no": 1}}),
        SimpleNamespace(id="c2", source_id="p1", ord=2, text="无关背景",
                        heading_path="", page_no=3, paragraph_idx=None,
                        offset_start=None, offset_end=None, meta={}),
    ]

    class FakeStore:
        def get_by_source(self, source_id, **kw):
            assert source_id == "p1"
            return rows

    monkeypatch.setattr("app.db.chunk_store.get_chunk_store", lambda: FakeStore())
    monkeypatch.setattr(ki, "_source_meta", lambda: {"p1": {"title": "专利P", "source_kind": "kb"}})

    ev = _ev("kb:p1#c0", title="权利要求1", snippet="一种除油剂配方", page=1, relevance=0.8)
    out = ki._stitch_patent_siblings([ev], max_siblings=2, meta={})
    idents = [e.identifier for e in out]
    assert idents[0] == "kb:p1#c0"
    assert "kb:p1#c1" in idents  # 同 claim_no 的兄弟被拼入
    assert "kb:p1#c2" not in idents  # 无 tag 的不拼
    sib = next(e for e in out if e.identifier == "kb:p1#c1")
    assert "同专利串联" in sib.title
    assert sib.relevance == pytest.approx(0.72)


def test_patent_stitch_no_tags_noop(monkeypatch):
    import app.services.kb_index as ki

    rows = [SimpleNamespace(id="c0", source_id="p1", ord=0, text="普通文献",
                            heading_path="", page_no=1, paragraph_idx=None,
                            offset_start=None, offset_end=None, meta={})]

    class FakeStore:
        def get_by_source(self, source_id, **kw):
            return rows

    monkeypatch.setattr("app.db.chunk_store.get_chunk_store", lambda: FakeStore())
    ev = _ev("kb:p1#c0", title="普通", snippet="x")
    out = ki._stitch_patent_siblings([ev], max_siblings=2, meta={})
    assert [e.identifier for e in out] == ["kb:p1#c0"]


def test_config_defaults_phase2():
    from app.config import get_settings

    s = get_settings()
    assert s.kb_children_retrieval_enabled is False  # A/B 决定前默认关闭
    assert s.kb_patent_stitch_enabled is True
    assert s.kb_patent_stitch_max_siblings == 2


def test_extract_numeric_facets():
    nums = extract_numeric_facets("耐蚀性达到72h，浓度50g/L，温度60°C的配方")
    assert any("72" in n and "h" in n for n in nums)
    assert any("50" in n for n in nums)
    assert extract_numeric_facets("没有数字的问题") == []


def test_assess_gap_plus_numeric_checkpoint():
    # Evidence mentions 耐蚀性 but not 72h → numeric gap flagged.
    ev = _ev("a", title="t", snippet="该配方耐蚀性良好")
    uncovered = assess_gap_plus(["耐蚀性"], [ev], query="耐蚀性达到72h的配方")
    assert any(u.startswith("num:") for u in uncovered)
    # With the number present → no numeric gap.
    ev2 = _ev("b", title="t", snippet="该配方耐蚀性达到72h")
    uncovered2 = assess_gap_plus(["耐蚀性"], [ev2], query="耐蚀性达到72h的配方")
    assert uncovered2 == []


def test_assess_gap_plus_backward_compatible():
    ev = _ev("a", title="耐蚀性", snippet="72h")
    assert assess_gap_plus(["耐蚀性"], [ev]) == []
    assert assess_gap_plus(["不存在"], [ev]) == ["不存在"]
