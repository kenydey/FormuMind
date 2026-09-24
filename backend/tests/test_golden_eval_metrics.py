"""Unit tests for golden eval MRR / Recall@k helpers (fast, not golden_eval marked)."""

from __future__ import annotations

from app.services.kb_query_test import keyword_rank_metrics


def test_keyword_rank_metrics_mrr_and_miss():
    hits = [
        {"title": " unrelated title", "snippet": "nothing here"},
        {"title": "环氧树脂防腐机理", "snippet": "交联网络"},
        {"title": "其他", "snippet": "x"},
    ]
    m = keyword_rank_metrics(hits, ["环氧", "防腐"], top_k=3)
    assert m["hit"] is True
    assert m["first_hit_rank"] == 2
    assert m["reciprocal_rank"] == 0.5
    assert m["matched_keyword"] == "环氧"

    miss = keyword_rank_metrics(hits, ["量子比特"], top_k=3)
    assert miss["hit"] is False
    assert miss["first_hit_rank"] is None
    assert miss["reciprocal_rank"] == 0.0


def test_keyword_rank_metrics_respects_top_k():
    hits = [
        {"title": "a", "snippet": "x"},
        {"title": "b", "snippet": "y"},
        {"title": "磷酸锌防锈", "snippet": "盐雾"},
    ]
    m = keyword_rank_metrics(hits, ["磷酸锌"], top_k=2)
    assert m["hit"] is False
    m3 = keyword_rank_metrics(hits, ["磷酸锌"], top_k=3)
    assert m3["hit"] is True
    assert m3["first_hit_rank"] == 3
    assert abs(m3["reciprocal_rank"] - 1 / 3) < 1e-9
