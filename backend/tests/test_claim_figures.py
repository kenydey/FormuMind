"""The offline claim verifier checks a claim's figures, not only its words (found by the full run after the D-1 change).

D-1 moved ``_token_set`` to character pairs so a plainly supported Chinese claim stops being judged unsupported. The side
effect: the words of a product category (水性 / 环氧 / 底漆) alone reached the "supported" threshold, so "HW-300 reaches
5000 h of salt spray" was *supported* by a generic report that mentions neither the product nor the figure - and the
abstention gate (tests/test_chat_abstention.py) stopped abstaining on fabricated specifications.
"""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.pipeline.claim_checker import ClaimVerdict, _figures, verify_claim_offline


def _ev(snippet: str, title: str = "技术报告") -> list[Evidence]:
    return [Evidence(source="literature", identifier="e1", title=title, snippet=snippet, relevance=0.9)]


def test_a_generic_report_does_not_support_a_figure_it_never_states():
    claim = verify_claim_offline("HW-300 新型水性环氧底漆中性盐雾达到 5000 小时", _ev("本报告介绍常规水性环氧底漆的制备工艺。"))
    assert claim.verdict is ClaimVerdict.unsupported and "5000" in claim.reason


def test_a_claim_with_one_figure_in_the_evidence_and_one_not_is_at_most_insufficient():
    claim = verify_claim_offline("水性环氧底漆干膜厚度 60 μm，中性盐雾达到 5000 小时", _ev("水性环氧底漆干膜厚度 60 μm，附着力 1 级。"))
    assert claim.verdict is ClaimVerdict.insufficient and "5000" in claim.reason and "60" not in claim.reason.split("not in evidence:")[-1]


def test_the_same_figures_in_other_spellings_still_support_the_claim():
    evidence = _ev("水性环氧底漆中性盐雾试验达到１，０００小时，干膜厚度 80.0 μm。")
    claim = verify_claim_offline("水性环氧底漆中性盐雾试验达到 1000 小时，干膜厚度 80 μm", evidence)
    assert claim.verdict is ClaimVerdict.supported


def test_a_claim_without_figures_is_judged_by_its_words_as_before():
    claim = verify_claim_offline("水性环氧底漆由环氧树脂和固化剂组成", _ev("水性环氧底漆由环氧树脂和固化剂组成，用于钢结构防腐。"))
    assert claim.verdict is ClaimVerdict.supported


def test_figures_fold_width_thousands_separators_and_trailing_zeros():
    assert _figures("１，０００ h, 0.50 mm, 2.5%") == {"1000", "0.5", "2.5"}
    assert _figures("无数字") == set()


def test_a_decimal_point_does_not_end_a_sentence():
    """The chat claim splitter cut after every full stop: "pH 6.5" became the claims "pH 6." and "5", a DOI "10." and "1000/x"."""
    from app.services.chat_claims import _SENTENCE_SPLIT

    def parts(text: str) -> list[str]:
        return [p for p in _SENTENCE_SPLIT.split(text) if p.strip()]

    assert parts("槽液 pH 6.5，温度 35.5 ℃。膜厚 80.0 μm") == ["槽液 pH 6.5，温度 35.5 ℃。", "膜厚 80.0 μm"]
    assert parts("see 10.1000/test for 0.5 mm. Next sentence.") == ["see 10.1000/test for 0.5 mm.", "Next sentence."]
    assert parts("ok with 10.1000/test") == ["ok with 10.1000/test"]
    assert parts("第一句！第二句？第三句。") == ["第一句！", "第二句？", "第三句。"]
