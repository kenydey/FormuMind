"""Runtime numeric-consistency check (P2-1).

Pure functions (stdlib only): extract unit-bearing numbers from an answer,
normalize units (with explicit conversions), and compare against the numbers
found in the cited evidence passages. Moved here from
``app/evals/rigor_rubric.py`` so the runtime chat path and the eval layer
share a single implementation.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

CITATION_RE = re.compile(r"\[\^(\d+)\]")


def extract_citation_indices(answer: str) -> list[int]:
    seen: list[int] = []
    for m in CITATION_RE.finditer(answer or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


_UNIT_ALIASES: dict[str, str] = {
    "°c": "c", "℃": "c",
    "μm": "um", "微米": "um", "um": "um",
    "mm": "mm", "毫米": "mm",
    "cm": "cm", "厘米": "cm",
    "m": "m", "米": "m",
    "h": "h", "小时": "h", "hr": "h", "hrs": "h", "hours": "h",
    "min": "min", "分钟": "min",
    "s": "s", "秒": "s",
    "%": "pct", "％": "pct",
    "ph": "ph",
    "mpa": "mpa", "kpa": "kpa", "pa": "pa",
    "g": "g", "克": "g",
    "kg": "kg", "千克": "kg",
    "mg": "mg", "毫克": "mg",
    "kg·cm": "kgcm", "kgcm": "kgcm",  # 冲击强度常用复合单位
    "ml": "ml", "毫升": "ml",
    "l": "l", "升": "l",
    "年": "yr",
}
_CONVERSIONS: dict[str, tuple[str, float]] = {
    "um": ("um", 1.0), "mm": ("um", 1000.0), "cm": ("um", 10000.0), "m": ("um", 1e6),
    "h": ("h", 1.0), "min": ("h", 1.0 / 60.0), "s": ("h", 1.0 / 3600.0),
    "mpa": ("mpa", 1.0), "kpa": ("mpa", 1e-3), "pa": ("mpa", 1e-6),
    "g": ("g", 1.0), "kg": ("g", 1000.0), "mg": ("g", 1e-3),
    "kgcm": ("kgcm", 1.0),
    "ml": ("ml", 1.0), "l": ("ml", 1000.0),
}
_UNIT_PATTERN = "|".join(
    sorted((re.escape(u) for u in _UNIT_ALIASES), key=len, reverse=True)
)
# ``(?<!\d)``: start only at the beginning of a digit run. Without it a run of N digits with no unit after it was retried
# from each of its N positions (each retry backing off one digit at a time through the whole unit alternation):
# quadratic — 4,000 digits took 3.5 s, and this runs on every answer and every evidence text. Matches are unchanged:
# a mid-run start can only end where the run-start attempt already ends.
_NUM_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(" + _UNIT_PATTERN + ")", re.IGNORECASE)
_PH_RE = re.compile(r"[pP][Hh]\s*(\d+(?:\.\d+)?)")
# pH 范围表达："pH 控制在 3.8-4.2" / "pH 8.5~9.5"（pH token 与数字不紧邻）。
# 限制中间非数字字符 ≤12，避免跨句误抓。
_PH_RANGE_RE = re.compile(
    r"[pP][Hh][^\d.]{0,12}?(\d+(?:\.\d+)?)\s*[~～\-–—]\s*(\d+(?:\.\d+)?)"
)
_RANGE_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*[-\u2013\u2014~\u301c]\s*(\d+(?:\.\d+)?)\s*(" + _UNIT_PATTERN + ")",
    re.IGNORECASE,
)


def _canon_unit(raw: str) -> str | None:
    return _UNIT_ALIASES.get(raw.strip().lower())


def _to_base(value: float, unit: str) -> tuple[str, float]:
    base, factor = _CONVERSIONS.get(unit, (unit, 1.0))
    return base, value * factor


def extract_numbers(text: str) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    seen: set[tuple[float, str]] = set()

    def _add(v: float, u: str | None) -> None:
        if u is None:
            return
        key = (v, u)
        if key not in seen:
            seen.add(key)
            out.append(key)

    # NFKC: "80 µm" with the MICRO SIGN (U+00B5 — what Word and most keyboards produce, and what pasted datasheets
    # carry) is a different character from the GREEK MU (U+03BC) the alias table lists, so the number silently was not
    # found; likewise full-width "８０" / "６５％" / "㎜" in Chinese documents. NFKC folds them all onto the forms below.
    src = unicodedata.normalize("NFKC", text or "")
    for m in _RANGE_RE.finditer(src):
        u = _canon_unit(m.group(3))
        _add(float(m.group(1)), u)
        _add(float(m.group(2)), u)
    for m in _NUM_RE.finditer(src):
        _add(float(m.group(1)), _canon_unit(m.group(2)))
    for m in _PH_RE.finditer(src):
        _add(float(m.group(1)), "ph")
    for m in _PH_RANGE_RE.finditer(src):
        _add(float(m.group(1)), "ph")
        _add(float(m.group(2)), "ph")
    return out


def _numbers_match(a: tuple[float, str], b: tuple[float, str]) -> bool:
    (av, au), (bv, bu) = a, b
    if au == bu:
        return math.isclose(av, bv, rel_tol=1e-6, abs_tol=1e-9)
    ab, avv = _to_base(av, au)
    bb, bvv = _to_base(bv, bu)
    return ab == bb and math.isclose(avv, bvv, rel_tol=1e-6, abs_tol=1e-9)


def check_answer_numbers(
    answer: str, evidence_texts: list[str]
) -> list[dict[str, str]]:
    """Runtime numeric check: numbers in ``answer`` vs cited passages.

    ``evidence_texts`` is 1:1 with the ``[^n]`` citation indices (index 0
    corresponds to ``[^1]``). Returns a list of failure dicts
    (``{"claim", "reason"}``); empty means pass. Pure function, no I/O.
    """
    answer_nums = extract_numbers(answer)
    if not answer_nums:
        return []
    indices = extract_citation_indices(answer)
    # v27 P2-17: 越界引用（如 [^99]）不再静默忽略 —— 记为明确 failure，
    # 提示可能是幻觉引用编号。
    failures: list[dict[str, str]] = []
    for n in indices:
        if not 1 <= n <= len(evidence_texts):
            failures.append(
                {
                    "claim": f"[^{n}]",
                    "reason": f"引用越界：答案引用了 [^{n}]，但只有 {len(evidence_texts)} 条证据（可能是幻觉编号）",
                }
            )
    cited = [
        evidence_texts[n - 1]
        for n in indices
        if 1 <= n <= len(evidence_texts)
    ]
    if not cited:
        # 答案含数字但无有效引用：不回退全 evidence 池，直接 fail。
        # 数字必须绑定到其引用 passage，不允许"未引用证据命中数字"蒙混。
        for v, u in answer_nums:
            failures.append(
                {
                    "claim": f"{v:g}{u}",
                    "reason": "答案含数字但无有效引用（数字无可绑定的引用来源）",
                }
            )
        return failures
    pool_nums: list[tuple[float, str]] = []
    for text in cited:
        pool_nums.extend(extract_numbers(text or ""))
    for v, u in answer_nums:
        if not any(_numbers_match((v, u), p) for p in pool_nums):
            failures.append(
                {
                    "claim": f"{v:g}{u}",
                    "reason": "答案中的数字在所引证据原文中无来源（亦无明确换算对应）",
                }
            )
    return failures


def format_numeric_note(failures: list[dict[str, str]]) -> str:
    """Render the user-facing annotation appended to an answer."""
    items = "; ".join(
        f"{f['claim']}（{f['reason']}）" for f in failures
    )
    return f"\n\n【数值核验】以下数字在所引证据原文中未找到对应来源，请谨慎采信：{items}"


def score_numeric_failures(
    answer: str, evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    """Eval-layer adapter: dict evidence (``text`` key) → score dict."""
    texts = [str((item or {}).get("text") or "") for item in evidence]
    failures = check_answer_numbers(answer, texts)
    answer_nums = extract_numbers(answer)
    if not answer_nums:
        return {"score": 1.0, "failures": []}
    ok = len(answer_nums) - len(failures)
    # v28: 同一数字可能因"引用越界"+"无有效引用"被记两次（P2-17 起），
    # 分数钳制在 [0, 1]，不出现负分。
    return {"score": max(0.0, round(ok / len(answer_nums), 4)), "failures": failures}
