"""C: Evidence authority bonus — patents > literature > seed > web."""

import pytest

from app.domain.schemas import Evidence
from app.services.search_scoring import evidence_authority_bonus


def _ev(source: str) -> Evidence:
    return Evidence(
        source=source,
        identifier="e1",
        title="t",
        snippet="s",
        relevance=0.5,
    )


class TestAuthorityBonus:
    def test_patents_highest(self):
        assert evidence_authority_bonus(_ev("USPTO")) == 0.12
        assert evidence_authority_bonus(_ev("EPO")) == 0.12

    def test_literature_second(self):
        assert evidence_authority_bonus(_ev("literature")) == 0.08
        assert evidence_authority_bonus(_ev("arxiv")) == 0.08

    def test_seed_third(self):
        assert evidence_authority_bonus(_ev("seed")) == 0.04

    def test_web_zero(self):
        assert evidence_authority_bonus(_ev("Tavily")) == 0.0
        assert evidence_authority_bonus(_ev("duckduckgo")) == 0.0
        assert evidence_authority_bonus(_ev("SerpAPI")) == 0.0

    def test_unknown_zero(self):
        assert evidence_authority_bonus(_ev("")) == 0.0
        assert evidence_authority_bonus(_ev("custom-source")) == 0.0

    def test_monotonic_ranking(self):
        """专利 > 文献 > 种子 > web — 排序语义完整。"""
        scores = [
            evidence_authority_bonus(_ev("USPTO")),
            evidence_authority_bonus(_ev("arxiv")),
            evidence_authority_bonus(_ev("seed")),
            evidence_authority_bonus(_ev("Tavily")),
        ]
        assert scores == sorted(scores, reverse=True)
        assert len(set(scores)) == 4  # 四档互不相同


class TestRankScoreWithAuthority:
    def test_boost_ranks_patent_over_web_same_relevance(self, monkeypatch):
        from app.services.literature import _rank_score_with_boost

        patent = _ev("USPTO")
        web = _ev("Tavily")
        # 相同 relevance + 相同 query 关键词 → 专利应排前（authority 加成）
        p_score, _ = _rank_score_with_boost(patent, {"zinc"}, {})
        w_score, _ = _rank_score_with_boost(web, {"zinc"}, {})
        assert p_score > w_score
