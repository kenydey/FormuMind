"""Wave 3 round3: 对抗指标单元测试。

锁定三个新对抗指标的判别行为：
- trap 答案（naive 系统输出）必须被检出（score 0.0）；
- 正确的期望行为（拒答/正确数值/标出矛盾）必须通过（score 1.0）；
- 非对抗 pair 走 not_applicable，不影响旧三项与 passed 判定。
"""

from __future__ import annotations

from app.evals.rigor_rubric import (
    ADVERSARIAL_METRICS,
    evaluate_rigor,
    metric_abstention_correctness,
    metric_contradiction_flagged,
    metric_value_correctness,
)
from app.resources.golden_rigor import golden_rigor_pairs


def _adv_pairs():
    return [p for p in golden_rigor_pairs if p.get("adversarial")]


def test_all_traps_are_detected_by_their_metric():
    """12 组 trap 答案各自对其期望指标必须打出 0.0（检出）。"""
    missed: list[str] = []
    for p in _adv_pairs():
        q = p["question"][:30]
        if p.get("expect_abstain"):
            r = metric_abstention_correctness(p["answer"], p)
            if r["score"] != 0.0:
                missed.append(f"{q}: abstention not flagged")
        if isinstance(p.get("expected_value"), dict):
            r = metric_value_correctness(p["answer"], p)
            if r["score"] != 0.0:
                missed.append(f"{q}: wrong value not flagged")
        if p.get("expect_contradiction_flag"):
            r = metric_contradiction_flagged(p["answer"], p["evidence"], p)
            if r["score"] != 0.0:
                missed.append(f"{q}: contradiction not flagged")
    assert not missed, f"traps evaded detection: {missed}"


def test_correct_behaviors_pass():
    """正确的期望行为必须通过三个新指标。"""
    ev = [
        {"identifier": "a", "title": "A", "text": "t1"},
        {"identifier": "b", "title": "B", "text": "t2"},
    ]
    r = metric_abstention_correctness(
        "现有证据不足，无法确定该产品的盐雾小时数。", {"expect_abstain": True}
    )
    assert r["score"] == 1.0
    r = metric_value_correctness(
        "冲击强度 50kg·cm[^1]。", {"expected_value": {"value": 50, "unit": "kg·cm"}}
    )
    assert r["score"] == 1.0
    r = metric_contradiction_flagged(
        "两项研究结论不一致[^1][^2]：A 认为安全，B 认为有风险。",
        ev,
        {"expect_contradiction_flag": True, "contradiction_evidence": [1, 2]},
    )
    assert r["score"] == 1.0


def test_value_correctness_unit_conversion_equivalence():
    """单位换算等价视为通过（如 0.08mm == 80μm）。"""
    r = metric_value_correctness(
        "膜厚 0.08mm[^1]。", {"expected_value": {"value": 80, "unit": "μm"}}
    )
    assert r["score"] == 1.0


def test_contradiction_missing_side_is_flagged():
    """只引单方（掩盖矛盾）必须被检出，即使有标记词也不行。"""
    ev = [
        {"identifier": "a", "title": "A", "text": "t1"},
        {"identifier": "b", "title": "B", "text": "t2"},
    ]
    r = metric_contradiction_flagged(
        "两项研究存在分歧[^1]，A 认为可行。",
        ev,
        {"expect_contradiction_flag": True, "contradiction_evidence": [1, 2]},
    )
    assert r["score"] == 0.0
    assert any("2" in f["claim"] or "未被引用" in f["reason"] for f in r["failures"])


def test_non_adversarial_pairs_are_not_applicable():
    """非对抗 pair：三个新指标 not_applicable，不影响 passed。"""
    std = [p for p in golden_rigor_pairs if not p.get("adversarial")]
    assert std, "standard pairs missing"
    for p in std[:5]:
        r = evaluate_rigor(p["answer"], p["evidence"], p["key_claims"])
        for m in ADVERSARIAL_METRICS:
            assert r[m].get("not_applicable"), f"{m} should be na for standard pair"
        # na 指标不拉低 passed（旧三项本身全过）
        assert r["passed"] is True


def test_evaluate_rigor_passed_ignores_na_metrics():
    """passed 判定跳过 not_applicable 指标（旧三项语义不变）。"""
    r = evaluate_rigor(
        "答案无引用",
        [{"identifier": "x", "title": "T", "text": "文本"}],
        [],
        pair_meta={"expect_abstain": True},
    )
    # citation_veracity 0 分 -> passed False；abstention 无标记 -> 0 分
    assert r["passed"] is False
    assert r["abstention_correctness"]["score"] == 0.0
    # 同样的答案若不声明期望，新指标 na，不影响 passed 的其他维度
    r2 = evaluate_rigor(
        "答案无引用", [{"identifier": "x", "title": "T", "text": "文本"}], []
    )
    assert r2["abstention_correctness"].get("not_applicable")
