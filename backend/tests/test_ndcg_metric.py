"""C-6: nDCG@k metric — pure-function behavior and graded fallback.

- ideal ordering → 1.0; reversed ordering < 1.0; no hits → 0.0
- top_k truncation; empty grade set → 0.0
- graded_keywords present → graded relevance; absent → binary fallback
"""
from __future__ import annotations

from app.services.kb_query_test import graded_relevance_for, ndcg_at_k


def _hit(*keywords: str) -> dict:
    return {"title": " ".join(keywords), "snippet": "", "text": ""}


GRADED = {"epoxy": 3, "cure": 2, "film": 1}


def test_ideal_ordering_scores_one() -> None:
    hits = [_hit("epoxy"), _hit("cure"), _hit("film")]
    assert ndcg_at_k(hits, GRADED, top_k=3) == 1.0


def test_reversed_ordering_scores_less_than_ideal() -> None:
    hits = [_hit("film"), _hit("cure"), _hit("epoxy")]
    score = ndcg_at_k(hits, GRADED, top_k=3)
    assert 0.0 < score < 1.0


def test_no_hits_scores_zero() -> None:
    assert ndcg_at_k([], GRADED, top_k=3) == 0.0


def test_no_matching_keywords_scores_zero() -> None:
    hits = [_hit("unrelated"), _hit("words")]
    assert ndcg_at_k(hits, GRADED, top_k=3) == 0.0


def test_empty_grades_scores_zero() -> None:
    assert ndcg_at_k([_hit("epoxy")], {}, top_k=3) == 0.0


def test_top_k_truncation() -> None:
    # Only the first 2 hits count: ideal pair → 1.0 even with a bad 3rd hit.
    hits = [_hit("epoxy"), _hit("cure"), _hit("unrelated")]
    assert ndcg_at_k(hits, GRADED, top_k=2) == 1.0
    # …but a misplaced 3rd hit drags the full top-3 below 1.0.
    # (2026-10-01 IDCG fix: [3,2,0] is already the ideal ordering of this
    # candidate set, so the "bad hit at the end" case now scores 1.0 —
    # the drag must come from a genuinely suboptimal ordering.)
    misplaced = [_hit("epoxy"), _hit("unrelated"), _hit("cure")]
    assert ndcg_at_k(misplaced, GRADED, top_k=3) < 1.0


def test_hit_takes_max_grade() -> None:
    # One hit containing both a grade-3 and a grade-1 keyword counts as 3.
    hits = [_hit("epoxy film")]
    assert ndcg_at_k(hits, GRADED, top_k=1) == 1.0


def test_graded_relevance_prefers_graded_keywords() -> None:
    entry = {
        "expected_keywords": ["a", "b"],
        "graded_keywords": {"a": 3, "b": 1},
    }
    assert graded_relevance_for(entry) == {"a": 3, "b": 1}


def test_graded_relevance_falls_back_to_binary() -> None:
    entry = {"expected_keywords": ["a", "b"]}
    assert graded_relevance_for(entry) == {"a": 1, "b": 1}


def test_golden_first_twenty_are_graded() -> None:
    from app.resources.golden_retrieval import golden_questions

    graded = [e for e in golden_questions if e.get("graded_keywords")]
    assert len(graded) == 20
    for e in graded:
        grades = e["graded_keywords"]
        assert set(grades) == set(e["expected_keywords"])
        assert all(1 <= v <= 3 for v in grades.values())


# ── IDCG bug 回归 (2026-10-01): IDCG 必须按候选 hits 的 relevance 构造 ──


def test_ndcg_never_exceeds_one_when_hits_share_top_keyword() -> None:
    """旧 bug 触发场景: 关键词数 < top_k 且多个 hit 命中同一高 grade 关键词。

    旧代码用关键词 grade 列表构造 IDCG ([3] 而非 [3,3,3,3,3,3]),
    nDCG 可达 3.3; 新代码必须恒 ≤ 1, 此处恰为 1.0。
    """
    graded = {"epoxy": 3}
    hits = [_hit("epoxy")] * 6
    score = ndcg_at_k(hits, graded, top_k=6)
    assert score == 1.0
    assert score <= 1.0


def test_ndcg_hand_computed_value() -> None:
    """手工验算: rels=[3,0,2] 时 DCG=3+0+2/log2(4)=4,
    IDCG=3+2/log2(3)+0, nDCG=round(4/4.2619, 4)=0.9386。"""
    hits = [_hit("epoxy"), _hit("unrelated"), _hit("cure")]
    assert ndcg_at_k(hits, GRADED, top_k=3) == 0.9386


def test_ndcg_monotonic_in_ordering_quality() -> None:
    """同一候选集: 排序越接近 relevance 降序, nDCG 越高。"""
    ideal = [_hit("epoxy"), _hit("cure"), _hit("film")]
    swapped = [_hit("cure"), _hit("epoxy"), _hit("film")]
    reversed_ = [_hit("film"), _hit("cure"), _hit("epoxy")]
    s_ideal = ndcg_at_k(ideal, GRADED, top_k=3)
    s_swapped = ndcg_at_k(swapped, GRADED, top_k=3)
    s_reversed = ndcg_at_k(reversed_, GRADED, top_k=3)
    assert s_ideal == 1.0
    assert s_ideal > s_swapped > s_reversed > 0.0
