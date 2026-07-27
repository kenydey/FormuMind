"""Task 2.6 tests: 答案引用绑定 [^n] — formatting, prompt, extraction, binding.

Four tests covering the full citation-binder pipeline:

  1. ``test_citation_formatting``  — CitationAnchor → numbered list + footnotes
  2. ``test_citation_extraction``   — regex extraction of [^n] from LLM answers
  3. ``test_prompt_template``       — prompt includes proper citation protocol
  4. ``test_binder_end_to_end``     — full LLM answer → clean answer + footnotes
"""

from __future__ import annotations

import pytest

from app.domain.citations import CitationAnchor
from app.services.citation_binder import (
    bind_citations,
    build_citation_prompt,
    build_footnotes_section,
    extract_citation_indices,
    extract_citation_indices_validated,
    format_answer_with_citations,
    format_citation_list,
    has_citation_markers,
    strip_existing_footnotes,
)


# ── Shared anchor fixtures ────────────────────────────────────────────────────


def _make_anchors() -> list[CitationAnchor]:
    """Return 3 realistic CitationAnchor objects for testing."""
    return [
        CitationAnchor(
            chunk_id="chunk-a",
            source_id="patent-EPO-001",
            text="环氧树脂（E-51）与聚酰胺固化剂在80°C下交联2小时，盐雾耐受≥720h。",
            page=3,
            paragraph=42,
            offset_start=1024,
            offset_end=2048,
        ),
        CitationAnchor(
            chunk_id="chunk-b",
            source_id="literature-arXiv-456",
            text="磷酸锌添加量3–5 wt%时，涂层附着力提升35%，耐盐雾从480h延长至960h。",
            page=7,
            paragraph=15,
            offset_start=512,
            offset_end=1024,
        ),
        CitationAnchor(
            chunk_id="chunk-c",
            source_id="patent-USPTO-789",
            text="硅烷偶联剂KH-560预处理铝基材，湿热附着力保持率>90%（60°C/95%RH/1000h）。",
            page=None,
            paragraph=None,
            offset_start=None,
            offset_end=None,
        ),
    ]


# ── Test 1: Citation formatting ───────────────────────────────────────────────


def test_citation_formatting():
    """format_citation_list + build_footnotes_section produce correct Markdown."""
    anchors = _make_anchors()

    # ── format_citation_list ──
    cite_list = format_citation_list(anchors)
    assert "[^1]" in cite_list
    assert "[^2]" in cite_list
    assert "[^3]" in cite_list
    assert "patent-EPO-001" in cite_list
    assert "pp. 3" in cite_list
    assert "¶42" in cite_list
    assert "环氧树脂" in cite_list
    # Anchor 3 has no page/paragraph — should still render.
    assert "patent-USPTO-789" in cite_list

    # ── build_footnotes_section (all) ──
    footnotes_all = build_footnotes_section(anchors)
    assert "[^1]:" in footnotes_all
    assert "[^2]:" in footnotes_all
    assert "[^3]:" in footnotes_all

    # ── build_footnotes_section (subset) ──
    footnotes_subset = build_footnotes_section(anchors, used_indices=[1, 3])
    assert "[^1]:" in footnotes_subset
    assert "[^2]:" not in footnotes_subset
    assert "[^3]:" in footnotes_subset

    # ── Empty list ──
    assert format_citation_list([]) == "_无可引用的文献。_"
    assert build_footnotes_section([]) == ""


# ── Test 2: Citation extraction ───────────────────────────────────────────────


def test_citation_extraction():
    """extract_citation_indices parses [^n] from realistic LLM answers."""
    # Single citations
    assert extract_citation_indices("环氧树脂具有优异附着力[^1]。") == [1]
    assert extract_citation_indices("No citations here.") == []

    # Multiple citations, deduplication
    text = (
        "环氧树脂交联后盐雾耐受≥720h [^1][^2]。"
        "磷酸锌进一步提升耐盐雾 [^2][^3]。"
        "这里证据不足。"
    )
    assert extract_citation_indices(text) == [1, 2, 3]

    # Non-sequential order → sorted output
    assert extract_citation_indices("See [^5], [^2], and [^8].") == [2, 5, 8]

    # Edge: zero and negative (should be ignored by source, but regex won't match ^0
    # since [^0] is not produced)
    assert extract_citation_indices("[^0]") == []

    # Large numbers
    assert extract_citation_indices("[^42][^99]") == [42, 99]

    # ── extract_citation_indices_validated ──
    valid, oor = extract_citation_indices_validated("[^1][^5][^99]", total_anchors=5)
    assert valid == [1, 5]
    assert oor == [99]

    valid, oor = extract_citation_indices_validated("[^1][^2]", total_anchors=3)
    assert valid == [1, 2]
    assert oor == []

    # ── has_citation_markers ──
    assert has_citation_markers("结论[^1]。") is True
    assert has_citation_markers("无引用标记。") is False
    assert has_citation_markers("[^1]") is True

    # ── strip_existing_footnotes ──
    answer_with_footnotes = (
        "结论摘要[^1]。\n\n"
        "[^1]: Source: src-42 — preview\n"
        "[^2]: Source: src-99 — preview\n"
    )
    stripped = strip_existing_footnotes(answer_with_footnotes)
    assert "结论摘要[^1]" in stripped
    assert "[^1]: Source:" not in stripped
    assert "[^2]: Source:" not in stripped


# ── Test 3: Citation-aware prompt template ────────────────────────────────────


def test_prompt_template():
    """build_citation_prompt produces a well-structured, citation-aware prompt."""
    anchors = _make_anchors()
    question = "环氧防腐涂料的最佳固化温度是多少？"

    prompt = build_citation_prompt(question, anchors, domain="防腐蚀涂料")

    # Must contain the question
    assert question in prompt

    # Must contain citation protocol instructions
    assert "引用规则" in prompt
    assert "[^n]" in prompt
    assert "证据不足" in prompt

    # Must contain the citation reference list with [^1], [^2], [^3]
    assert "[^1]" in prompt
    assert "[^2]" in prompt
    assert "[^3]" in prompt
    assert "patent-EPO-001" in prompt
    assert "literature-arXiv-456" in prompt
    assert "patent-USPTO-789" in prompt

    # Domain hint should be present
    assert "防腐蚀涂料" in prompt

    # Custom system instruction override
    custom_sys = "自定义角色：你是化学专家。"
    prompt2 = build_citation_prompt(
        question, anchors, system_instruction=custom_sys
    )
    assert custom_sys in prompt2
    # "引用规则" in the *trailing instruction* is still present (it's part of
    # the prompt structure, not the system instruction block).
    assert "引用规则" in prompt2  # still in trailing instructions


# ── Test 4: Full binding pipeline (end-to-end) ────────────────────────────────


def test_binder_end_to_end():
    """bind_citations processes a realistic LLM answer end-to-end."""
    anchors = _make_anchors()

    # Simulated LLM answer with [^n] markers
    raw_answer = (
        "## 环氧防腐涂料固化温度分析\n\n"
        "根据文献，环氧树脂（E-51型）与聚酰胺固化剂在80°C下交联2小时，"
        "盐雾耐受可达720小时以上[^1]。\n\n"
        "进一步研究表明，添加3–5 wt%磷酸锌可提升涂层附着力约35%，"
        "同时将耐盐雾性能从480h延长至960h[^2]。\n\n"
        "关于铝基材预处理，硅烷偶联剂KH-560表现出优异的湿热附着力保持率[^3]。\n\n"
        "关于固化温度上限对VOC排放的影响，当前文献证据不足。\n\n"
        "[^1]: Source: fake footnote the LLM generated\n"
    )

    binding = bind_citations(raw_answer, anchors)

    # answer should have markers intact
    assert "[^1]" in binding["answer"]
    assert "[^2]" in binding["answer"]
    assert "[^3]" in binding["answer"]

    # LLM-generated footnotes should be stripped
    assert "[^1]: Source: fake footnote" not in binding["answer"]

    # used_citations should have 3 entries
    assert len(binding["used_citations"]) == 3
    indices = [pair[0] for pair in binding["used_citations"]]
    assert 1 in indices
    assert 2 in indices
    assert 3 in indices

    # footnotes section should have [^1]: [^2]: [^3]:
    assert "[^1]:" in binding["footnotes"]
    assert "[^2]:" in binding["footnotes"]
    assert "[^3]:" in binding["footnotes"]

    # ── format_answer_with_citations convenience ──
    combined = format_answer_with_citations(raw_answer, anchors)
    assert "[^1]" in combined
    assert "[^1]:" in combined  # footnotes are appended


def test_binder_no_citations_in_answer():
    """bind_citations handles answers with zero citation markers gracefully."""
    anchors = _make_anchors()
    answer = "该领域目前缺乏可靠文献支持，暂无法给出明确结论。"

    binding = bind_citations(answer, anchors)
    assert binding["answer"] == answer
    assert binding["footnotes"] == ""
    assert binding["used_citations"] == []


def test_binder_empty_anchors():
    """bind_citations handles empty anchor list gracefully."""
    answer = "This answer has [^1] but no anchors to map to."
    binding = bind_citations(answer, [])
    assert binding["answer"] == "This answer has [^1] but no anchors to map to."
    assert binding["footnotes"] == ""
    assert binding["used_citations"] == []
