"""P3-6a: patent tag extraction golden — 20 bilingual groups.

``extract_patent_tags`` is pure-regex first-match at ingest time; a wrong
extraction pollutes the claim↔example stitching context (and the P2-2
patent golden relies on these tags). 20 groups pin the behaviour.
"""
from __future__ import annotations

import pytest

from app.services.metadata_tags import extract_patent_tags

CASES = [
    # (text, heading_path, expected_subset)
    # ── 中文权利要求 ──
    ("权利要求 1 所述的水性环氧底漆，其特征在于……", "", {"claim_no": 1}),
    ("如权利要求书第 3 项所述的组合物……", "", {"claim_no": 3}),
    ("权利要求10-12 任一项所述的方法", "", {"claim_no": 10}),  # first match
    # ── 中文实施例 ──
    ("实施例 2：按表 1 配方制备底漆，盐雾 1200 小时。", "", {"example_no": 2}),
    ("具体实施方式 1 中，磷酸锌用量为 8%。", "", {"example_no": 1}),
    # ── 英文 claim ──
    ("Claim 1. A waterborne epoxy primer comprising……", "", {"claim_no": 1}),
    ("as described in claims 3-5, the composition……", "", {"claim_no": 3}),
    ("See claim No. 2 for the curing conditions.", "", {"claim_no": 2}),
    # ── 英文 example / embodiment ──
    ("Example 1: the primer was prepared as follows……", "", {"example_no": 1}),
    ("Example No. 2 showed 1500h salt spray resistance.", "", {"example_no": 2}),
    ("In embodiment 3, zinc phosphate was replaced.", "", {"example_no": 3}),
    ("Embodiments 1-4 were tested for adhesion.", "", {"example_no": 1}),
    # ── 混合 chunk：claim + example 共存 ──
    (
        "权利要求 1 的保护范围通过实施例 2 得以验证，盐雾 1200 小时。",
        "",
        {"claim_no": 1, "example_no": 2},
    ),
    (
        "The scope of claim 4 is demonstrated in Example 3 below.",
        "",
        {"claim_no": 4, "example_no": 3},
    ),
    # ── section_title ──
    (
        "本发明涉及防腐底漆。",
        "说明书 > 具体实施方式",
        {"section_title": "具体实施方式"},
    ),
    (
        "Background text.",
        "Description > Detailed Description",
        {"section_title": "Detailed Description"},
    ),
    # ── 负例：无编号语境不抽取 ──
    ("the claims were examined by the examiner", "", {}),
    ("for example, the coating may contain zinc", "", {}),
    ("纯描述性文字，无任何编号引用。", "", {}),
    ("权利要求书附图说明", "", {}),  # "权利要求书" 后无数字
]


@pytest.mark.parametrize("text,heading,expected", CASES)
def test_patent_tag_golden(text, heading, expected):
    tags = extract_patent_tags(text, heading)
    for key, val in expected.items():
        assert tags.get(key) == val, f"text={text!r} tags={tags}"
    if not expected:
        assert "claim_no" not in tags
        assert "example_no" not in tags
