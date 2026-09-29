"""P2-2: evidence-insufficiency abstention hard gate (threshold 0.5).

Gate is exercised through the real deterministic offline verifier
(verify_claim_offline) + _apply_answer_gates — no LLM involved.
Includes patent-tag golden cases (claim_no/example_no stitched siblings).
"""
import pytest

from app.api.chat import _ABSTAIN_TEMPLATE, _apply_answer_gates
from app.config import get_settings
from app.domain.schemas import Evidence
from app.pipeline.claim_checker import verify_claim_offline


def _ev(title, snippet, source="patent"):
    return Evidence(
        source=source, identifier="p1", title=title, snippet=snippet, relevance=0.9
    )


def _gate(answer, citations, claims, settings=None):
    s = settings or get_settings()
    verified = [verify_claim_offline(c, citations) for c in claims]
    gated, _, abstained = _apply_answer_gates(
        "q", answer, citations, [], verified, s
    )
    return gated, abstained, verified


def test_zero_recall_abstains():
    gated, abstained, _ = _gate("某新型底漆盐雾可达2000小时。", [], ["某新型底漆盐雾可达2000小时"])
    assert abstained is True
    assert "证据不足" in gated


def test_all_unsupported_abstains():
    # 语义对应 golden_rigor 4 组 expect_abstain：伪造产品规格，证据无支撑。
    ev = [_ev("水性环氧底漆技术报告", "本报告介绍常规水性环氧底漆的制备工艺。", source="literature")]
    claims = [
        "HW-300 新型水性环氧底漆中性盐雾达到 5000 小时",
        "HW-300 底漆附着力达到 15MPa",
    ]
    answer = "HW-300 新型水性环氧底漆中性盐雾达到 5000 小时[^1]，附着力 15MPa[^1]。"
    gated, abstained, verified = _gate(answer, ev, claims)
    assert all(v.verdict.value == "unsupported" for v in verified)
    assert abstained is True
    assert gated.startswith("证据不足")


def test_supported_answer_not_abstained():
    ev = [_ev("盐雾试验报告", "中性盐雾试验达到 1000 小时，附着力 5MPa。", source="literature")]
    claims = ["中性盐雾试验达到 1000 小时", "附着力 5MPa"]
    answer = "中性盐雾试验达到 1000 小时[^1]，附着力 5MPa[^1]。"
    gated, abstained, verified = _gate(answer, ev, claims)
    assert abstained is False
    assert gated == answer


def test_threshold_boundary():
    # 1/2 unsupported = 0.5，不大于阈值 → 不拒答；2/3 > 0.5 → 拒答。
    ev = [_ev("工艺说明", "磷化槽液总酸度控制在 18-22 点。", source="literature")]
    claims_ok = ["磷化槽液总酸度控制在 18-22 点", "槽液温度应控制在 999 度"]
    v = [verify_claim_offline(c, ev) for c in claims_ok]
    s = get_settings()
    _, _, ab = _apply_answer_gates("q", "a", ev, [], v, s)
    assert ab is False  # 0.5 不触发（严格大于）
    claims_bad = claims_ok + ["槽液 pH 应为 99"]
    v2 = [verify_claim_offline(c, ev) for c in claims_bad]
    _, _, ab2 = _apply_answer_gates("q", "a", ev, [], v2, s)
    assert ab2 is True


def test_claim_check_disabled_fail_open():
    s = get_settings()
    gated, _, abstained = _apply_answer_gates(
        "q", "某答案", [], [], None, s
    )
    # verified=None（检查关闭）但零召回仍拒答：零召回不依赖 claim 检查。
    assert abstained is True
    ev = [_ev("t", "s")]
    gated2, _, ab2 = _apply_answer_gates("q", "某答案", ev, [], None, s)
    assert ab2 is False and gated2 == "某答案"


def test_patent_tag_golden_insufficient_abstains():
    # 专利标签 golden A：claim_no/example_no stitch 召回了兄弟 chunk，
    # 但答案的数值论断在专利文本中无支撑 → 拒答。
    ev = [
        _ev(
            "CN1234567B 权利要求书",
            "Claim 1：一种水性防腐底漆，包含成膜树脂 30-50 重量份。",
        ),
        _ev(
            "CN1234567B 说明书实施例",
            "Example 2：按实施例 1 制备底漆，中性盐雾 720 小时无异常。",
        ),
    ]
    claims = ["该专利底漆中性盐雾达到 3000 小时", "该专利底漆含锌粉 80 份"]
    answer = "该专利底漆中性盐雾达到 3000 小时[^1][^2]，含锌粉 80 份[^1]。"
    gated, abstained, verified = _gate(answer, ev, claims)
    assert abstained is True
    assert "证据不足" in gated


def test_patent_tag_golden_sufficient_answers():
    # 专利标签 golden B：同样的专利证据，答案论断有支撑 → 正常回答。
    ev = [
        _ev(
            "CN1234567B 权利要求书",
            "Claim 1：一种水性防腐底漆，包含成膜树脂 30-50 重量份。",
        ),
        _ev(
            "CN1234567B 说明书实施例",
            "Example 2：按实施例 1 制备底漆，中性盐雾 720 小时无异常。",
        ),
    ]
    claims = ["该专利底漆包含成膜树脂 30-50 重量份", "实施例 2 中性盐雾 720 小时无异常"]
    answer = "该专利底漆包含成膜树脂 30-50 重量份[^1]，实施例 2 中性盐雾 720 小时无异常[^2]。"
    gated, abstained, _ = _gate(answer, ev, claims)
    assert abstained is False
    assert gated == answer


def test_abstain_template_uses_eval_marker():
    # 模板首句必须含评测层 _ABSTAIN_MARKERS 成员，保持口径一致。
    from app.evals.rigor_rubric import _ABSTAIN_MARKERS

    assert any(m in _ABSTAIN_TEMPLATE for m in _ABSTAIN_MARKERS)
