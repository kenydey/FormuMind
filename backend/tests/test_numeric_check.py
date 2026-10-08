"""P2-1: numeric-consistency runtime check (shared impl with eval layer)."""
from app.evals.rigor_rubric import (
    extract_citation_indices,
    extract_numbers,
    metric_numeric_consistency,
)
from app.services.numeric_check import (
    check_answer_numbers,
    format_numeric_note,
    score_numeric_failures,
)


def test_extract_numbers_units_and_ranges():
    nums = extract_numbers("膜厚 25μm，盐雾 1000小时，pH 控制在 3.8-4.2")
    assert (25.0, "um") in nums
    assert (1000.0, "h") in nums
    assert (3.8, "ph") in nums and (4.2, "ph") in nums


def test_unit_conversion_match():
    # 1000μm == 1mm：换算后应命中，不判 fail。
    assert check_answer_numbers("膜厚 1mm[^1]。", ["涂层厚度 1000μm"]) == []


def test_mismatch_detected():
    failures = check_answer_numbers("盐雾 1200小时[^1]。", ["中性盐雾试验达到 1000小时"])
    assert len(failures) == 1
    assert failures[0]["claim"] == "1200h"


def test_numbers_without_citation_fail_closed():
    failures = check_answer_numbers("盐雾 1200小时。", ["中性盐雾试验达到 1200小时"])
    assert len(failures) == 1
    assert "无有效引用" in failures[0]["reason"]


def test_no_numbers_passes_silently():
    assert check_answer_numbers("该工艺适用于钢铁基材。", ["钢铁基材适用"]) == []


def test_format_numeric_note_contains_claim():
    note = format_numeric_note(
        [{"claim": "1200h", "reason": "无来源"}]
    )
    assert "1200h" in note and "数值核验" in note


def test_eval_metric_delegates_to_shared_impl():
    # 评测层 metric 与运行时共用单实现：行为一致。
    ev = [{"text": "中性盐雾试验达到1000小时"}]
    ok = metric_numeric_consistency("盐雾1000小时[^1]。", ev)
    bad = metric_numeric_consistency("盐雾1200小时[^1]。", ev)
    assert ok["score"] == 1.0 and ok["failures"] == []
    assert bad["score"] == 0.0 and len(bad["failures"]) == 1
    # 与 score_numeric_failures 一致
    assert score_numeric_failures("盐雾1200小时[^1]。", ev) == bad


def test_mutation_conversion_removed_would_fail():
    # 区分力：若去掉单位换算，1mm vs 1000μm 应被判 fail（证明换算真实生效）。
    nums = extract_numbers("1mm")
    assert nums == [(1.0, "mm")]
    pool = extract_numbers("1000μm")
    assert pool == [(1000.0, "um")]
    # 换算路径：mm→um 系数 1000
    assert check_answer_numbers("膜厚 1mm[^1]。", ["涂层厚度 1000微米"]) == []


def test_check_answer_numbers_dangling_citation_failure():
    """v27 P2-17: 越界引用记为明确 failure，不再静默忽略。"""
    fails = check_answer_numbers("数值为 720 h[^99]", ["盐雾 720 h"])
    assert any("[^99]" in f["claim"] and "越界" in f["reason"] for f in fails)
