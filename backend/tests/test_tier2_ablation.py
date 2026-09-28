"""Wave 1 (2026-09-28) tier-2 ablation: locks the eval-gated rollout decision.

Decision (from A/B on long-form coating/corrosion evidence, deterministic
extractive mock LLM):
- Tier 2 defaults ON. Under tight token budgets tier-1's mechanical 400-char
  truncation drops buried key numbers (rigor numeric_consistency 0.0), while
  tier-2 query-focused summaries preserve them (1.0), at the cost of 1 batched
  LLM call per query that has long (>=1500 char) evidence.
- With generous budgets both arms score 1.0, but tier-2 still cuts evidence
  tokens by ~90%.

Override: FORMUMIND_QUERY_COMPRESS_LLM_ENABLED=false.

NOTE: the mock LLM below is deliberately extractive (copies source sentences
containing digits). It measures the best case a faithful summarizer gives;
a hallucinating LLM is a separate risk, mitigated by the prompt's
no-fabrication instruction, _apply_summaries ignoring unknown ids, and the
fail-open fallback to tier 1 (tested below).
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.schemas import Evidence
from app.evals.rigor_rubric import evaluate_rigor
from app.services import query_aware_compression as qac

FILLER = (
    "涂层耐蚀性评价需综合考虑多种因素。基材表面状态直接影响涂层附着，"
    "喷砂除锈等级、表面粗糙度、清洁度均需达标。涂料施工环境温湿度控制严格，"
    "温度过低延缓固化，湿度过高引发针孔。涂层厚度均匀性通过多点测量保证，"
    "边角部位易偏薄需复涂。颜料体积浓度影响屏蔽性能，过高导致孔隙增多。"
    "涂装间隔超时需拉毛处理，否则层间附着不良。烘烤固化曲线应实测验证。"
)


def _long(key_part: str, target: int = 2600) -> str:
    """Key facts buried mid-text inside realistic filler."""
    out = FILLER
    while len(out) < target // 2:
        out += FILLER
    out = out[: target // 2] + key_part
    while len(out) < target:
        out += FILLER
    return out[:target]


LONG_1 = _long(
    "本试验体系经过 1000 小时连续盐雾后，划线处单边锈蚀扩展为 1.5mm，"
    "非划线区未见明显锈蚀。按重防腐配套要求，单边锈蚀扩展限值为 2mm，"
    "本体系满足要求。试验温度控制在 35°C，氯化钠溶液浓度为 5%。"
)
LONG_2 = _long(
    "工艺窗口要求锌系磷化膜重控制在 2~5g/m² 范围内，膜重过低耐蚀不足，"
    "过高则影响涂层附着力。磷化工作液 pH 应维持在 2.8-3.2，"
    "pH 偏高会加速沉渣生成并导致膜层粗糙。干燥后 24 小时内喷涂底漆。"
)
LONG_3 = _long(
    "本产品推荐固化条件为 80-120°C 下烘烤 30 分钟，"
    "低于 80°C 时需延长至 60 分钟。干膜厚度建议 80~125μm，"
    "分两道施工达到。固化不足会导致涂层发软、耐溶剂性差。"
)

QUESTION = "盐雾1000小时后锈蚀扩展多少？磷化膜重和pH控制范围？环氧固化条件？"
ANSWER = (
    "1000 小时盐雾后划线处单边锈蚀扩展 1.5mm，限值 2mm，满足要求[^1]。"
    "锌系磷化膜重控制在 2~5g/m²，工作液 pH 维持 2.8-3.2[^2]。"
    "环氧在 80-120°C 下固化 30 分钟[^3]。"
)
KEY_CLAIMS = [
    {"text": "1000小时盐雾锈蚀扩展1.5mm限值2mm",
     "keywords": ["1000小时", "1.5mm", "2mm"]},
    {"text": "磷化膜重2~5g/m² pH2.8-3.2",
     "keywords": ["5g", "pH", "3.2"]},
    {"text": "环氧80-120°C固化30分钟",
     "keywords": ["120°C", "30分钟"]},
]

assert len(LONG_1) >= qac.MIN_SNIPPET_CHARS_FOR_LLM
assert len(LONG_2) >= qac.MIN_SNIPPET_CHARS_FOR_LLM
assert len(LONG_3) >= qac.MIN_SNIPPET_CHARS_FOR_LLM


def _ev(i: int, title: str, snippet: str) -> Evidence:
    return Evidence(
        source="literature", identifier=f"abl-{i}", title=title,
        snippet=snippet, relevance=0.9,
    )


def _sources() -> list[Evidence]:
    return [
        _ev(1, "盐雾试验报告", LONG_1),
        _ev(2, "磷化工艺手册", LONG_2),
        _ev(3, "环氧底漆技术数据", LONG_3),
        _ev(4, "富锌底漆说明", "富锌底漆干膜锌粉含量不低于 80%。"),
    ]


def _extractive_mock(prompt: str, **kwargs):
    """Faithful summarizer stand-in: keeps digit-bearing sentences."""
    blocks = re.findall(r"\[id=([^\]]+)\]\s*[^\n]*\n(.*?)(?=\n\[id=|\Z)", prompt, re.S)
    summaries = []
    for sid, text in blocks:
        sents = re.split(r"(?<=[。；])", text)
        kept = [s.strip() for s in sents if re.search(r"\d", s)][:6]
        summaries.append({"id": sid.strip(), "summary": "".join(kept)[:600]})
    return {"summaries": summaries}


def _settings(tier2: bool, budget: int = 12000) -> SimpleNamespace:
    return SimpleNamespace(
        query_compress_enabled=True,
        query_compress_llm_enabled=tier2,
        query_compress_token_budget=budget,
        evidence_reviewer_model="",
    )


def _rigor_dicts(evs: list[Evidence]) -> list[dict]:
    return [
        {"identifier": e.identifier, "title": e.title, "doi": "",
         "text": f"{e.title}. {e.snippet}"}
        for e in evs
    ]


def _run_arm(tier2: bool, budget: int) -> tuple[list[Evidence], int]:
    """Mirror of paperqa_engine.py: tier2 first (when enabled), then tier1."""
    sources = _sources()
    s = _settings(tier2, budget)
    calls = []

    def counting(prompt, **kwargs):
        calls.append(1)
        return _extractive_mock(prompt, **kwargs)

    with patch("app.services.llm.complete_json", side_effect=counting):
        if qac.query_compress_llm_enabled(s):
            sources = qac.llm_compress_evidence(QUESTION, sources, settings=s)
        out = qac.compress_evidence(QUESTION, sources, token_budget=budget)
    return out, len(calls)


def _tokens(evs: list[Evidence]) -> int:
    return sum(qac.estimate_tokens(f"{e.title}. {e.snippet}") for e in evs)


# ── switch behavior ──────────────────────────────────────────────────────────


def test_tier2_default_on_in_config():
    from app.config import Settings

    assert Settings.model_fields["query_compress_llm_enabled"].default is True


def test_tier2_explicit_off_disables_llm():
    out, calls = _run_arm(tier2=False, budget=12000)
    assert calls == 0
    assert all(qac.LLM_SUMMARY_PREFIX not in (e.snippet or "") for e in out)


def test_tier2_threshold_boundary():
    """1499 chars → no LLM call; 1500 chars → exactly one."""
    s = _settings(True)
    short = _ev(9, "t", "x" * (qac.MIN_SNIPPET_CHARS_FOR_LLM - 1))
    long_ = _ev(10, "t", "x" * qac.MIN_SNIPPET_CHARS_FOR_LLM)
    with patch("app.services.llm.complete_json") as m:
        m.side_effect = lambda prompt, **kw: {"summaries": []}
        qac.llm_compress_evidence(QUESTION, [short], settings=s)
        assert m.call_count == 0
        qac.llm_compress_evidence(QUESTION, [long_], settings=s)
        assert m.call_count == 1


# ── A/B: quality under budget pressure ───────────────────────────────────────


def test_ab_tight_budget_numeric_consistency():
    """The decisive ablation result.

    Tight budget (300 tokens): tier-1's mechanical truncation drops the
    buried key numbers → numeric_consistency 0.0. Tier-2 summaries preserve
    them → 1.0. This is what the default-ON decision rests on.
    """
    out_a, _ = _run_arm(tier2=False, budget=300)
    out_b, _ = _run_arm(tier2=True, budget=300)
    rigor_a = evaluate_rigor(ANSWER, _rigor_dicts(out_a), KEY_CLAIMS)
    rigor_b = evaluate_rigor(ANSWER, _rigor_dicts(out_b), KEY_CLAIMS)
    assert rigor_a["numeric_consistency"]["score"] == 0.0
    assert rigor_b["numeric_consistency"]["score"] == 1.0
    # Citation anchors and coverage must not regress in either arm.
    assert rigor_a["citation_veracity"]["score"] == 1.0
    assert rigor_b["citation_veracity"]["score"] == 1.0
    assert rigor_b["coverage"]["score"] == 1.0


def test_ab_generous_budget_token_savings():
    """Generous budget: same rigor in both arms, tier-2 cuts ~90% tokens."""
    out_a, calls_a = _run_arm(tier2=False, budget=12000)
    out_b, calls_b = _run_arm(tier2=True, budget=12000)
    rigor_a = evaluate_rigor(ANSWER, _rigor_dicts(out_a), KEY_CLAIMS)
    rigor_b = evaluate_rigor(ANSWER, _rigor_dicts(out_b), KEY_CLAIMS)
    for metric in ("citation_veracity", "coverage", "numeric_consistency"):
        assert rigor_a[metric]["score"] == 1.0
        assert rigor_b[metric]["score"] == 1.0
    assert calls_a == 0
    assert calls_b == 1  # single batched call, not one per item
    assert _tokens(out_b) < 0.3 * _tokens(out_a)


def test_ab_citation_anchors_preserved():
    """Tier-2 must never touch identifier/title — only snippet is rewritten."""
    out_b, _ = _run_arm(tier2=True, budget=12000)
    by_id = {e.identifier: e for e in out_b}
    assert by_id["abl-1"].title == "盐雾试验报告"
    assert by_id["abl-1"].snippet.startswith(qac.LLM_SUMMARY_PREFIX)


# ── fail-open ────────────────────────────────────────────────────────────────


def test_tier2_llm_failure_falls_back_to_tier1():
    sources = _sources()
    s = _settings(True)
    with patch("app.services.llm.complete_json", side_effect=RuntimeError("boom")):
        out = qac.llm_compress_evidence(QUESTION, sources, settings=s)
    # Falls back to tier-1 result: same items, original snippets untouched.
    assert [e.identifier for e in out] == [e.identifier for e in sources]
    assert all(qac.LLM_SUMMARY_PREFIX not in (e.snippet or "") for e in out)


def test_tier2_empty_llm_reply_falls_back():
    sources = _sources()
    s = _settings(True)
    with patch("app.services.llm.complete_json", return_value={"summaries": []}):
        out = qac.llm_compress_evidence(QUESTION, sources, settings=s)
    assert [e.identifier for e in out] == [e.identifier for e in sources]
