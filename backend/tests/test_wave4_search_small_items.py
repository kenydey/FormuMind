"""W4-6 搜索小件三件套测试。

* P0-19: citedBy 按论文年龄归一化（``age_normalized_citation_score``）
* P0-18: 日期/域名过滤 + LLM 输出日期绝对校验（``citation_date_guard``）
* P0-15: 引用定位器（``CitationLocator`` + manifest item locator）
"""
from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest

from app.domain.citations import CitationAnchor, CitationLocator
from app.domain.schemas import Evidence
from app.services import literature_manifest as lm
from app.services.citation_date_guard import (
    FUTURE_DATE_PENALTY,
    evidence_year,
    filter_evidence_by_date,
    filter_evidence_by_domain,
    future_pub_date_penalty,
    parse_pub_year,
    validate_llm_citation_dates,
)
from app.services.search_scoring import age_normalized_citation_score


def _ev(**kw) -> Evidence:
    base = dict(
        source="OpenAlex",
        identifier="10.1/xyz",
        title="Epoxy coating",
        snippet="adhesion",
        relevance=0.5,
    )
    base.update(kw)
    return Evidence(**base)


# ── P0-19: 年龄归一化 ──────────────────────────────────────────────

class TestAgeNormalizedCitationScore:
    def test_new_paper_beats_old_with_same_total(self):
        old = _ev(cited_by=1000, pub_year=2000)
        new = _ev(cited_by=100, pub_year=date.today().year - 1)
        assert age_normalized_citation_score(new) > age_normalized_citation_score(old)

    def test_no_data_returns_zero(self):
        assert age_normalized_citation_score(_ev()) == 0.0
        assert age_normalized_citation_score(_ev(cited_by=0, pub_year=2020)) == 0.0
        assert age_normalized_citation_score(_ev(cited_by=None, pub_year=2020)) == 0.0

    def test_capped(self):
        s = age_normalized_citation_score(_ev(cited_by=10_000_000, pub_year=2024))
        assert 0.0 < s <= 0.12

    def test_missing_year_uses_age_one(self):
        # 无年份 → age_years=1，不惩罚（rate = cited_by）
        s = age_normalized_citation_score(_ev(cited_by=10))
        assert s > 0.0

    def test_openalex_work_to_evidence_carries_counts(self):
        from app.services.search_providers import _openalex_work_to_evidence

        w = {
            "doi": "https://doi.org/10.1/xyz",
            "id": "https://openalex.org/W1",
            "display_name": "Epoxy coating",
            "cited_by_count": 42,
            "publication_year": 2021,
            "open_access": {"is_oa": True},
            "best_oa_location": {"pdf_url": None},
        }
        ev = _openalex_work_to_evidence(w, 0, 0)
        assert ev.cited_by == 42
        assert ev.pub_year == 2021

    def test_rank_fusion_uses_normalized_score(self):
        from app.services.literature import _rank_score_with_boost

        q_kw = {"epoxy"}
        qctx: dict = {}
        plain = _ev(cited_by=None)
        cited = _ev(cited_by=500, pub_year=date.today().year - 1)
        s_plain, _ = _rank_score_with_boost(plain, q_kw, qctx)
        s_cited, _ = _rank_score_with_boost(cited, q_kw, qctx)
        assert s_cited > s_plain


# ── P0-18: 日期/域名过滤 + 绝对校验 ────────────────────────────────

class TestCitationDateGuard:
    def test_parse_pub_year(self):
        assert parse_pub_year(2023) == 2023
        assert parse_pub_year("2023") == 2023
        assert parse_pub_year("2023-05-01") == 2023
        assert parse_pub_year("发表于2023年") == 2023
        assert parse_pub_year(None) is None
        assert parse_pub_year("abc") is None
        assert parse_pub_year(99) is None

    def test_evidence_year_prefers_pub_year(self):
        assert evidence_year(_ev(pub_year=2020, pub_date="2021-03-01")) == 2020
        assert evidence_year(_ev(pub_date="2021-03-01")) == 2021
        assert evidence_year(_ev()) is None

    def test_filter_by_date_keeps_unknown(self):
        evs = [_ev(pub_year=2020), _ev(pub_year=2024), _ev()]
        out = filter_evidence_by_date(evs, date_from=2022)
        assert [e.pub_year for e in out] == [2024, None]
        out2 = filter_evidence_by_date(evs, date_from="2022", date_to="2023-12-31")
        assert [e.pub_year for e in out2] == [None]

    def test_filter_by_date_noop(self):
        evs = [_ev(pub_year=2020)]
        assert filter_evidence_by_date(evs) == evs

    def test_filter_by_domain(self):
        a = _ev(url="https://patents.google.com/patent/US1", source="Google Patents")
        b = _ev(url="https://example.com/x", source="web")
        c = _ev(source="OpenAlex")  # 无 URL，回退 source 匹配
        out = filter_evidence_by_domain([a, b, c], ["patents.google.com", "openalex"])
        assert out == [a, c]
        assert filter_evidence_by_domain([a, b], None) == [a, b]
        assert filter_evidence_by_domain([a, b], []) == [a, b]

    def test_validate_llm_citation_dates_flags_future(self):
        cur = date.today().year
        md = (
            f"正文引用[^1]。\n\n"
            f"[^1]: 某论文，Nature ({cur + 2}).\n"
            f"[^2]: 另一篇，Science ({cur - 1}).\n"
        )
        findings = validate_llm_citation_dates(md)
        assert len(findings) == 1
        assert findings[0]["footnote"] == 1
        assert findings[0]["year"] == cur + 2
        assert findings[0]["reason"] == "future_dated_citation"

    def test_validate_llm_citation_dates_clean(self):
        cur = date.today().year
        md = f"正文[^1]。\n\n[^1]: 某论文 ({cur - 3}).\n"
        assert validate_llm_citation_dates(md) == []
        assert validate_llm_citation_dates("") == []

    def test_future_pub_date_penalty(self):
        cur = date.today().year
        assert future_pub_date_penalty(_ev(pub_year=cur + 2)) == FUTURE_DATE_PENALTY
        assert future_pub_date_penalty(_ev(pub_year=cur)) == 0.0
        assert future_pub_date_penalty(_ev()) == 0.0

    def test_federated_search_filter_params_backward_compat(self):
        from app.services.federated_search import FederatedSearchEngine

        sig = inspect.signature(FederatedSearchEngine.search)
        for name in ("date_from", "date_to", "domain_allowlist"):
            assert name in sig.parameters
            assert sig.parameters[name].default is None


# ── P0-15: 引用定位器 ─────────────────────────────────────────────

class TestCitationLocator:
    def test_schema_roundtrip(self):
        loc = CitationLocator(page=3, figure="2")
        assert loc.to_dict() == {"page": 3, "figure": "2"}
        back = CitationLocator.from_dict({"page": 3, "figure": "2", "table": None})
        assert back == loc

    def test_from_dict_invalid(self):
        assert CitationLocator.from_dict(None) is None
        assert CitationLocator.from_dict({}) is None
        assert CitationLocator.from_dict({"page": "abc"}) is None
        assert CitationLocator.from_dict("nope") is None  # type: ignore[arg-type]

    def test_anchor_render_with_locator(self):
        a = CitationAnchor(
            chunk_id="c1",
            source_id="doc.pdf",
            text="t",
            locator=CitationLocator(page=3, figure="2", table="1"),
        )
        txt = a.to_citation_text()
        assert "pp. 3" in txt and "Fig. 2" in txt and "Table 1" in txt

    def test_anchor_render_backward_compat(self):
        a = CitationAnchor(chunk_id="c1", source_id="doc.pdf", text="t", page=3, paragraph=2)
        assert a.to_citation_text() == "Source: doc.pdf, pp. 3, ¶2"
        b = CitationAnchor(chunk_id="c1", source_id="doc.pdf", text="t")
        assert b.locator is None
        assert b.to_citation_text() == "Source: doc.pdf"


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


def _seed(project_id: str = "p1") -> dict:
    man = lm.empty_manifest(project_id)
    man["items"] = [
        {
            "id": "a",
            "title": "Epoxy coating",
            "doi": "10.1/aaa",
            "source": "lit",
            "snippet": "adhesion",
            "evidence_class": "search_hit",
            "screening": "match",
        },
    ]
    return lm.save_manifest(man)


class TestManifestItemLocator:
    def test_set_and_get_roundtrip(self, tmp_data):
        _seed()
        lm.set_item_locator("p1", "a", {"page": 5, "figure": "2", "table": None})
        assert lm.get_item_locator("p1", "a") == {"page": 5, "figure": "2"}

    def test_clear_with_empty(self, tmp_data):
        _seed()
        lm.set_item_locator("p1", "a", {"page": 5})
        lm.set_item_locator("p1", "a", {})
        assert lm.get_item_locator("p1", "a") is None

    def test_invalid_page_rejected(self, tmp_data):
        _seed()
        with pytest.raises(ValueError):
            lm.set_item_locator("p1", "a", {"page": "abc"})
        with pytest.raises(ValueError):
            lm.set_item_locator("p1", "a", {"page": 0})

    def test_unknown_item(self, tmp_data):
        _seed()
        with pytest.raises(LookupError):
            lm.set_item_locator("p1", "zzz", {"page": 1})
        assert lm.get_item_locator("p1", "zzz") is None

    def test_locator_survives_freeze_and_digest_stable(self, tmp_data):
        _seed()
        d0 = lm.compute_digest(["a"], lm.load_manifest("p1")["items"])
        lm.freeze("p1")
        # freeze 后精化定位器：不解除冻结、不改变 corpus 摘要
        lm.set_item_locator("p1", "a", {"page": 7, "table": "1"})
        man = lm.load_manifest("p1")
        assert man["frozen"] is not None
        d1 = lm.compute_digest(["a"], man["items"])
        assert d0 == d1
        frozen = lm.frozen_items("p1")
        assert frozen[0]["locator"] == {"page": 7, "table": "1"}
