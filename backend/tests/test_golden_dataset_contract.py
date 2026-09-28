"""Wave 3 round2: golden rigor 数据集契约测试。

锁定数据集结构（字段完整性、引用有效性、版本单调）与硬性约束
（数据集内不得出现品牌名），避免静默改标准。
"""

from __future__ import annotations

import re

from app.evals.rigor_rubric import CITATION_RE
from app.resources.golden_rigor import DATASET_VERSION, golden_rigor_pairs

_SOURCE_KEYS = ("identifier", "title", "doi", "url")


def _all_strings(obj) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_all_strings(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_all_strings(v))
    return out


def test_dataset_version_is_positive_int():
    assert isinstance(DATASET_VERSION, int) and DATASET_VERSION >= 3


def test_dataset_has_30_or_more_pairs():
    assert len(golden_rigor_pairs) >= 30, (
        f"expected 30+ pairs, got {len(golden_rigor_pairs)}"
    )


def test_adversarial_pairs_present():
    adv = [p for p in golden_rigor_pairs if p.get("adversarial")]
    assert 10 <= len(adv) <= 15, f"expected 10-15 adversarial pairs, got {len(adv)}"


def test_adversarial_pairs_have_decidable_expectations():
    from app.evals.rigor_rubric import _canon_unit

    for i, p in enumerate(golden_rigor_pairs):
        if not p.get("adversarial"):
            continue
        tag = f"adversarial pair[{i}]"
        has_abstain = bool(p.get("expect_abstain"))
        has_value = isinstance(p.get("expected_value"), dict)
        has_contra = bool(p.get("expect_contradiction_flag"))
        assert has_abstain or has_value or has_contra, (
            f"{tag} 必须声明 expect_abstain / expected_value / "
            "expect_contradiction_flag 之一"
        )
        if has_value:
            ev = p["expected_value"]
            assert isinstance(ev.get("value"), (int, float)), f"{tag} expected_value.value 非数值"
            assert _canon_unit(str(ev.get("unit") or "")) is not None, (
                f"{tag} expected_value.unit 无法归一化: {ev.get('unit')!r}"
            )
        if has_contra:
            ce = p.get("contradiction_evidence") or []
            n_ev = len(p["evidence"])
            assert ce and all(1 <= int(x) <= n_ev for x in ce), (
                f"{tag} contradiction_evidence 非法: {ce!r}"
            )


def test_no_duplicate_questions():
    questions = [p.get("question") for p in golden_rigor_pairs]
    assert len(questions) == len(set(questions)), "duplicate question found"


def test_no_brand_names_in_dataset():
    # 硬性约束：数据集内不得出现品牌名（大小写不敏感）。
    # 检查串动态拼接，避免字面量本身触发仓库级 grep。
    banned = ["".join(["via", "nt"]), "防安途"]
    for s in _all_strings(golden_rigor_pairs):
        low = s.lower()
        for b in banned:
            assert b not in low and b not in s, f"brand name leaked: {s[:60]!r}"


def test_each_pair_has_required_fields():
    for i, p in enumerate(golden_rigor_pairs):
        assert isinstance(p.get("question"), str) and p["question"].strip(), f"pair[{i}] question"
        assert isinstance(p.get("answer"), str) and p["answer"].strip(), f"pair[{i}] answer"
        assert isinstance(p.get("evidence"), list) and p["evidence"], f"pair[{i}] evidence"
        assert isinstance(p.get("key_claims"), list) and p["key_claims"], f"pair[{i}] key_claims"


def test_citations_within_evidence_range():
    for i, p in enumerate(golden_rigor_pairs):
        n_ev = len(p["evidence"])
        indices = [int(m.group(1)) for m in CITATION_RE.finditer(p["answer"])]
        assert indices, f"pair[{i}] answer has no [^n] citations"
        for n in indices:
            assert 1 <= n <= n_ev, f"pair[{i}] orphan citation [^{n}] (evidence={n_ev})"


def test_each_evidence_has_resolvable_source():
    for i, p in enumerate(golden_rigor_pairs):
        for j, ev in enumerate(p["evidence"]):
            assert any(str(ev.get(k) or "").strip() for k in _SOURCE_KEYS), (
                f"pair[{i}] evidence[{j}] has no resolvable source"
            )
            assert isinstance(ev.get("text"), str) and ev["text"].strip(), (
                f"pair[{i}] evidence[{j}] empty text"
            )


def test_each_key_claim_has_keywords():
    for i, p in enumerate(golden_rigor_pairs):
        for j, c in enumerate(p["key_claims"]):
            assert isinstance(c.get("text"), str) and c["text"].strip(), (
                f"pair[{i}] claim[{j}] empty text"
            )
            kws = [k for k in (c.get("keywords") or []) if str(k).strip()]
            assert kws, f"pair[{i}] claim[{j}] empty keywords"
