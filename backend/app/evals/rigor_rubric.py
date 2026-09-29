"""W6-1 · P1-38: evals 严谨性 rubric。

三项确定性（无 LLM、纯本地）指标，用于回归门禁：

1. ``citation_veracity`` —— 答案中每个 ``[^n]`` 引用必须命中本次证据集合
   （按位置 1..len(evidence)），且被引证据至少有一个可解析来源
   （identifier / title / doi / url 四取一非空）。孤儿引用记 fail；
   答案没有任何引用时亦记 fail（不给空引用满分）。
2. ``coverage`` —— golden 标注的关键 claim 覆盖占比。claim 被"覆盖"当且仅当
   其关键词在答案中出现（>=50%）且答案带有至少一个有效引用。
   未覆盖但证据中有支撑的 claim 记 fail（"有证据没引用"）。
3. ``numeric_consistency`` —— 答案中带单位的数字，经单位归一化（含明确换算，
   如 mm<->μm、h<->min）后，必须在所引 passage/snippet 原文出现；无来源数字记 fail。
   数字只在其引用 passage 中核验，不回退全证据池；答案含数字但无有效引用时直接 fail。

4. ``abstention_correctness`` —— 对抗用例（``pair_meta["expect_abstain"]=True``）：
   证据不足以回答时，答案是否拒答或明确声明证据不足。含拒答标记记 1.0，
   否则 0.0。非对抗 pair 记 ``not_applicable``（1.0，不参与判定）。
5. ``contradiction_flagged`` —— 对抗用例（``pair_meta["expect_contradiction_flag"]=True``）：
   证据间结论冲突时，答案是否明确标出矛盾（含标记词）且引用冲突双方
   （``pair_meta["contradiction_evidence"]``）。缺标记或漏引冲突方记 0.0。
   非对抗 pair 记 ``not_applicable``。
6. ``value_correctness`` —— 对抗用例（``pair_meta["expected_value"]={"value","unit"}``）：
   答案中的数值经单位归一化后是否与期望值一致（防"数字在引用中有但张冠李戴"，
   这是 ``numeric_consistency`` 覆盖不到的）。非对抗 pair 记 ``not_applicable``。

新指标在 ``evaluate_rigor`` 中通过 ``pair_meta`` 传入；``not_applicable`` 的
指标不参与 ``passed`` 判定。旧三项行为、阈值保持不变。

每项输出 ``{"score": float | None, "failures": [{"claim", "reason"}]}``；
``score`` 为 None 表示该指标内部异常（fail-open：记 error，不炸主流程）。
对抗指标额外返回 ``"not_applicable": True`` 表示本 pair 不适用。

输入约定（与 ``citation_binder`` / W4-2 冻结 evidence 兼容的最小结构）::

    evidence = [
        {"identifier": "...", "title": "...", "doi": "...", "url": "...",
         "text": "<passage/snippet 原文>", "page": 3, "figure": "2", "table": None},
        ...
    ]
    key_claims = [{"text": "...", "keywords": ["盐雾", "1000小时"]}, ...]
"""

from __future__ import annotations

import re
from typing import Any

# P2-1: 数值抽取/单位归一化单实现移到 app/services/numeric_check.py，
# 运行时与评测层共用；此处 re-export 保持对外 import 路径兼容。
from ..services.numeric_check import (
    CITATION_RE,
    _canon_unit,
    _numbers_match,
    check_answer_numbers,
    extract_citation_indices,
    extract_numbers,
    score_numeric_failures,
)

__all__ = [
    "CITATION_RE",
    "DEFAULT_THRESHOLDS",
    "ADVERSARIAL_METRICS",
    "evaluate_rigor",
    "extract_citation_indices",
    "extract_numbers",
    "metric_abstention_correctness",
    "metric_citation_veracity",
    "metric_contradiction_flagged",
    "metric_coverage",
    "metric_numeric_consistency",
    "metric_value_correctness",
]

# 默认阈值（与 config.py::evals_rigor_thresholds 保持一致；gate 以 config 为准）。
DEFAULT_THRESHOLDS: dict[str, float] = {
    "citation_veracity": 1.0,
    "coverage": 0.8,
    "numeric_consistency": 1.0,
}

_SOURCE_KEYS = ("identifier", "title", "doi", "url")


def _norm(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[\s\u3000\-–—_.,;:!?，。；：！？、（）()\[\]【】\"'“”‘’·/\\]+", "", t)
    return t


def metric_citation_veracity(
    answer: str, evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    indices = extract_citation_indices(answer)
    failures: list[dict[str, str]] = []
    if not indices:
        # 零引用：不再给满分 —— 无任何引用时无法确认依据，记 fail。
        failures.append(
            {
                "claim": "(no citations)",
                "reason": "答案中没有任何 [^n] 引用，无法确认其依据",
            }
        )
        return {"score": 0.0, "failures": failures}
    valid = 0
    for n in indices:
        if n < 1 or n > len(evidence):
            failures.append(
                {
                    "claim": f"[^{n}]",
                    "reason": (
                        f"orphan citation: [^{n}]超出本次证据集合范围 "
                        f"(共{len(evidence)}条证据)"
                    ),
                }
            )
            continue
        item = evidence[n - 1] or {}
        if not any(str(item.get(k) or "").strip() for k in _SOURCE_KEYS):
            failures.append(
                {
                    "claim": f"[^{n}]",
                    "reason": "cited evidence has no resolvable source "
                    "(identifier/title/doi/url 全空)",
                }
            )
            continue
        valid += 1
    score = 1.0 if not indices else valid / len(indices)
    return {"score": round(score, 4), "failures": failures}


def _claim_hit_ratio(claim: dict[str, Any], text_norm: str) -> float:
    kws = [k for k in (claim.get("keywords") or []) if str(k).strip()]
    if not kws:
        return 1.0
    hits = sum(1 for k in kws if _norm(str(k)) in text_norm)
    return hits / len(kws)


def metric_coverage(
    answer: str,
    evidence: list[dict[str, Any]],
    key_claims: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    claims = [c for c in (key_claims or []) if isinstance(c, dict)]
    if not claims:
        return {"score": 1.0, "failures": []}
    answer_norm = _norm(answer)
    evidence_norm = [_norm(str(e.get("text") or "")) for e in evidence]
    has_valid_citation = any(
        1 <= n <= len(evidence) for n in extract_citation_indices(answer)
    )
    failures: list[dict[str, str]] = []
    covered = 0
    for claim in claims:
        text = str(claim.get("text") or claim.get("keywords") or "")
        if _claim_hit_ratio(claim, answer_norm) >= 0.5 and has_valid_citation:
            covered += 1
            continue
        supported = any(
            _claim_hit_ratio(claim, ev) >= 0.5 for ev in evidence_norm
        )
        reason = (
            "claim 有证据支撑但答案未覆盖/未引用"
            if supported
            else "答案未覆盖该关键 claim（证据中亦无支撑）"
        )
        failures.append({"claim": text, "reason": reason})
    score = covered / len(claims)
    return {"score": round(score, 4), "failures": failures}


def metric_numeric_consistency(
    answer: str, evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    """答案中带单位数字的证据一致性（P2-1 起与运行时共用单实现）。"""
    return score_numeric_failures(answer, evidence)


# ---------------------------------------------------------------------------
# 对抗指标（Wave 3 round3）：只在 pair_meta 声明对应期望时生效，
# 否则返回 not_applicable（score 1.0，不参与 passed 判定）。
# ---------------------------------------------------------------------------

ADVERSARIAL_METRICS = (
    "abstention_correctness",
    "contradiction_flagged",
    "value_correctness",
)

_ABSTAIN_MARKERS = (
    "证据不足", "无法确定", "无法回答", "信息不足", "不能确定",
    "暂无数据", "没有足够", "不足以回答", "无法给出",
    "insufficient evidence", "cannot determine", "not enough evidence",
    "unable to determine",
)

_CONTRADICTION_MARKERS = (
    "不一致", "矛盾", "冲突", "相反", "有差异", "存在分歧", "相悖",
    "contradict", "inconsist", "conflict", "disagree", "divergen",
)


def _na() -> dict[str, Any]:
    return {"score": 1.0, "failures": [], "not_applicable": True}


def metric_abstention_correctness(
    answer: str, pair_meta: dict[str, Any] | None
) -> dict[str, Any]:
    """该拒答时是否拒答/声明证据不足。"""
    if not (pair_meta or {}).get("expect_abstain"):
        return _na()
    norm = _norm(answer)
    if any(_norm(m) in norm for m in _ABSTAIN_MARKERS):
        return {"score": 1.0, "failures": []}
    return {
        "score": 0.0,
        "failures": [
            {
                "claim": "(expected abstention)",
                "reason": (
                    "现有证据不足以回答该问题，期望拒答或明确声明证据不足，"
                    "但答案给出了确定性结论"
                ),
            }
        ],
    }


def metric_contradiction_flagged(
    answer: str,
    evidence: list[dict[str, Any]],
    pair_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """证据结论冲突时，答案是否明确标出矛盾且引用冲突双方。"""
    meta = pair_meta or {}
    if not meta.get("expect_contradiction_flag"):
        return _na()
    want = [int(x) for x in (meta.get("contradiction_evidence") or [])]
    norm = _norm(answer)
    has_marker = any(_norm(m) in norm for m in _CONTRADICTION_MARKERS)
    cited = set(extract_citation_indices(answer))
    missing = [n for n in want if n not in cited]
    failures: list[dict[str, str]] = []
    if not has_marker:
        failures.append(
            {
                "claim": "(expected contradiction flag)",
                "reason": (
                    "证据间结论存在冲突，但答案未明确标出矛盾 "
                    "（和稀泥/只报一面）"
                ),
            }
        )
    for n in missing:
        failures.append(
            {
                "claim": f"[^{n}]",
                "reason": "冲突证据方未被引用（只引单方 = 掩盖矛盾）",
            }
        )
    return {"score": 1.0 if not failures else 0.0, "failures": failures}


def metric_value_correctness(
    answer: str, pair_meta: dict[str, Any] | None
) -> dict[str, Any]:
    """答案数值（单位归一化后）是否与期望值一致。

    覆盖 numeric_consistency 的盲区：数字在所引 passage 中存在、
    但张冠李戴（量级/单位/归属错误）的 trap。
    """
    meta = pair_meta or {}
    exp = meta.get("expected_value")
    if not isinstance(exp, dict):
        return _na()
    try:
        target_v = float(exp["value"])
    except (TypeError, ValueError):
        return {
            "score": None,
            "failures": [],
            "error": f"bad expected_value: {exp!r}",
        }
    target_u = _canon_unit(str(exp.get("unit") or ""))
    if target_u is None:
        return {
            "score": None,
            "failures": [],
            "error": f"unknown unit in expected_value: {exp.get('unit')!r}",
        }
    nums = extract_numbers(answer)
    if not nums:
        return {
            "score": 0.0,
            "failures": [
                {"claim": "(expected value)", "reason": "答案未给出任何带单位数值"}
            ],
        }
    target = (target_v, target_u)
    if any(_numbers_match(n, target) for n in nums):
        return {"score": 1.0, "failures": []}
    return {
        "score": 0.0,
        "failures": [
            {
                "claim": f"{target_v:g}{target_u}(expected)",
                "reason": (
                    "答案数值与期望值不一致（可能张冠李戴/单位/量级错误）"
                ),
            }
        ],
    }


def evaluate_rigor(
    answer: str,
    evidence: list[dict[str, Any]] | None,
    key_claims: list[dict[str, Any]] | None = None,
    thresholds: dict[str, float] | None = None,
    pair_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ev = [e for e in (evidence or []) if isinstance(e, dict)]
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update({k: float(v) for k, v in thresholds.items() if k in th})

    metrics: dict[str, dict[str, Any]] = {}
    for name, fn, args in (
        ("citation_veracity", metric_citation_veracity, (answer, ev)),
        ("coverage", metric_coverage, (answer, ev, key_claims)),
        ("numeric_consistency", metric_numeric_consistency, (answer, ev)),
        ("abstention_correctness", metric_abstention_correctness, (answer, pair_meta)),
        ("contradiction_flagged", metric_contradiction_flagged, (answer, ev, pair_meta)),
        ("value_correctness", metric_value_correctness, (answer, pair_meta)),
    ):
        try:
            metrics[name] = fn(*args)
        except Exception as exc:
            metrics[name] = {
                "score": None,
                "failures": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    passed = all(
        m.get("not_applicable")
        or (m["score"] is not None and m["score"] >= th.get(name, 1.0))
        for name, m in metrics.items()
    )
    return {**metrics, "passed": passed, "thresholds": th}
