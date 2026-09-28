"""W6-1 · P1-38: 严谨性 rubric 测试 —— 三项指标各有正/反例。"""

from __future__ import annotations

import pytest

from app.evals.rigor_rubric import (
    evaluate_rigor,
    extract_citation_indices,
    extract_numbers,
    metric_citation_veracity,
    metric_coverage,
    metric_numeric_consistency,
)


def _ev(*items):
    return list(items)


def _src(identifier="doc-1", title="测试文献", text="盐雾 1000 小时后无锈蚀。"):
    return {"identifier": identifier, "title": title, "text": text}


# ── 引用真实性 ─────────────────────────────────────────────────────────────


def test_veracity_all_valid():
    ans = "盐雾 1000 小时无锈蚀[^1]，符合要求[^2]。"
    m = metric_citation_veracity(ans, _ev(_src("a"), _src("b")))
    assert m["score"] == 1.0 and m["failures"] == []


def test_veracity_orphan_citation_fails():
    ans = "结论成立[^3]。"  # 只有 2 条证据
    m = metric_citation_veracity(ans, _ev(_src("a"), _src("b")))
    assert m["score"] == 0.0
    assert len(m["failures"]) == 1
    assert "orphan" in m["failures"][0]["reason"]


def test_veracity_cited_evidence_without_source_fails():
    ans = "结论成立[^1]。"
    m = metric_citation_veracity(ans, _ev({"text": "某文本"}))
    assert m["score"] == 0.0
    assert "resolvable source" in m["failures"][0]["reason"]


def test_veracity_no_citations_fails():
    # B-6 修复：零引用不再给满分（此前 vacuous 1.0 让门禁看不见无引用答案）
    m = metric_citation_veracity("没有引用的陈述。", _ev(_src()))
    assert m["score"] == 0.0
    assert len(m["failures"]) == 1
    assert "没有" in m["failures"][0]["reason"] and "引用" in m["failures"][0]["reason"]


def test_extract_citation_indices_dedup_order():
    assert extract_citation_indices("a[^2]b[^1]c[^2]") == [2, 1]


# ── 覆盖率 ─────────────────────────────────────────────────────────────────


def _claims():
    return [
        {"text": "盐雾 1000 小时无锈蚀", "keywords": ["盐雾", "1000小时"]},
        {"text": "pH 维持 8.5", "keywords": ["pH", "8.5"]},
    ]


def test_coverage_full():
    ans = "盐雾 1000 小时后无锈蚀[^1]；体系 pH 维持 8.5[^2]。"
    ev = _ev(_src(text="盐雾 1000 小时后无锈蚀。"), _src(text="体系 pH 维持 8.5。"))
    m = metric_coverage(ans, ev, _claims())
    assert m["score"] == 1.0 and m["failures"] == []


def test_coverage_partial_with_supported_evidence_fails():
    # 第二个 claim 答案没提，但证据里有支撑 → "有证据没引用"
    ans = "盐雾 1000 小时后无锈蚀[^1]。"
    ev = _ev(_src(text="盐雾 1000 小时后无锈蚀。"), _src(text="体系 pH 维持 8.5。"))
    m = metric_coverage(ans, ev, _claims())
    assert m["score"] == 0.5
    assert len(m["failures"]) == 1
    assert "有证据支撑但答案未覆盖" in m["failures"][0]["reason"]


def test_coverage_unsupported_claim_reason():
    ans = "盐雾 1000 小时后无锈蚀[^1]。"
    ev = _ev(_src(text="盐雾 1000 小时后无锈蚀。"))
    m = metric_coverage(ans, ev, _claims())
    assert m["score"] == 0.5
    assert "证据中亦无支撑" in m["failures"][0]["reason"]


def test_coverage_keywords_without_citation_not_covered():
    # 关键词出现但答案无任何引用 → 不算覆盖
    ans = "盐雾 1000 小时后无锈蚀，体系 pH 维持 8.5。"
    ev = _ev(_src(text="盐雾 1000 小时后无锈蚀。"), _src(text="体系 pH 维持 8.5。"))
    m = metric_coverage(ans, ev, _claims())
    assert m["score"] == 0.0


def test_coverage_no_claims_vacuous_pass():
    m = metric_coverage("答案[^1]。", _ev(_src()), None)
    assert m["score"] == 1.0


# ── 数值一致性 ─────────────────────────────────────────────────────────────


def test_numeric_direct_hit():
    ans = "盐雾 1000 小时后无锈蚀[^1]。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 1.0 and m["failures"] == []


def test_numeric_unsourced_number_fails():
    ans = "盐雾 9999 小时后无锈蚀[^1]。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 0.0
    assert len(m["failures"]) == 1
    assert "无来源" in m["failures"][0]["reason"]


def test_numeric_explicit_conversion_passes():
    # 明确换算：1.2mm == 1200μm
    ans = "干膜厚度 1.2mm[^1]。"
    ev = _ev(_src(text="干膜厚度 1200μm，分两道施工。"))
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 1.0


def test_numeric_only_checks_cited_evidence():
    # 答案引用 [^1]，数字只在未被引用的 [^2] 中出现 → fail
    ans = "盐雾 1000 小时[^1]。"
    ev = _ev(
        _src("a", text="无关文本。"),
        _src("b", text="中性盐雾 1000 小时。"),
    )
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 0.0


def test_numeric_no_numbers_vacuous_pass():
    m = metric_numeric_consistency("无数字的陈述[^1]。", _ev(_src()))
    assert m["score"] == 1.0


def test_extract_numbers_range_and_ph():
    nums = extract_numbers("80-120°C 固化，pH 8.5，膜重 2~5g")
    assert (80.0, "c") in nums and (120.0, "c") in nums
    assert (8.5, "ph") in nums
    assert (2.0, "g") in nums and (5.0, "g") in nums


# ── 总入口与阈值 ───────────────────────────────────────────────────────────


def test_evaluate_rigor_passed_flag():
    ans = "盐雾 1000 小时后无锈蚀[^1]。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    claims = [{"text": "盐雾 1000 小时无锈蚀", "keywords": ["盐雾", "1000小时"]}]
    r = evaluate_rigor(ans, ev, claims)
    assert r["passed"] is True
    assert set(r["thresholds"]) == {
        "citation_veracity",
        "coverage",
        "numeric_consistency",
    }


def test_evaluate_rigor_threshold_breach():
    ans = "盐雾 9999 小时后无锈蚀[^1]。"  # 无来源数字
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    r = evaluate_rigor(ans, ev, None)
    assert r["passed"] is False
    assert r["numeric_consistency"]["score"] == 0.0


def test_evaluate_rigor_custom_thresholds():
    ans = "部分覆盖[^1]。"
    ev = _ev(_src(text="部分覆盖。"))
    claims = [
        {"text": "c1", "keywords": ["部分"]},
        {"text": "c2", "keywords": ["不存在的关键词xyz"]},
    ]
    r = evaluate_rigor(ans, ev, claims, thresholds={"coverage": 0.4})
    assert r["coverage"]["score"] == 0.5
    assert r["passed"] is True  # 0.5 >= 0.4


def test_evaluate_rigor_fail_open_on_bad_evidence():
    # evidence 含非 dict 脏数据 → 被过滤，不抛异常
    r = evaluate_rigor("答案[^1]。", [None, "junk", _src()], None)
    assert r["citation_veracity"]["score"] == 1.0


def test_config_rigor_thresholds_defaults(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        th = get_settings().evals_rigor_thresholds
        assert th["citation_veracity"] == 1.0
        assert th["coverage"] == 0.8
        assert th["numeric_consistency"] == 1.0
    finally:
        get_settings.cache_clear()


# ── B-6 回归：无引用数字不再回退全 evidence 池 ──────────────────────────────


def test_b6_numeric_no_citations_fails_without_pool_fallback():
    # 答案有数字但零引用：此前 `pool = cited or evidence` 回退全池，
    # 未引用证据含相同数字即判一致 → score=1.0；现直接 fail。
    ans = "盐雾 1000 小时后无锈蚀。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 0.0
    assert len(m["failures"]) == 1
    assert "无有效引用" in m["failures"][0]["reason"]


def test_b6_numeric_orphan_citations_only_fails():
    # 引用全部越界（无有效引用）同样 fail，不回退
    ans = "盐雾 1000 小时后无锈蚀[^9]。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 0.0


def test_b6_numeric_no_numbers_still_vacuous_pass():
    # 无数字答案：保持 vacuous pass（不受 B-6 影响）
    m = metric_numeric_consistency("无数字的陈述。", _ev(_src()))
    assert m["score"] == 1.0 and m["failures"] == []


def test_b6_evaluate_rigor_gate_catches_unsourced_numbers():
    # 门禁级回归：无引用数字答案整体 passed 必须为 False
    ans = "盐雾 1000 小时后无锈蚀。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    r = evaluate_rigor(ans, ev, None)
    assert r["passed"] is False
    assert r["citation_veracity"]["score"] == 0.0
    assert r["numeric_consistency"]["score"] == 0.0


def test_b6_valid_citation_path_unchanged():
    # 正常引用路径不受影响：数字在所引 passage 中命中 → 1.0
    ans = "盐雾 1000 小时后无锈蚀[^1]。"
    ev = _ev(_src(text="中性盐雾 1000 小时，样板无锈蚀。"))
    m = metric_numeric_consistency(ans, ev)
    assert m["score"] == 1.0 and m["failures"] == []
    v = metric_citation_veracity(ans, ev)
    assert v["score"] == 1.0 and v["failures"] == []
