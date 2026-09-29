"""Rule-based patent metadata tags for chunk ingest (Phase 0, zero LLM cost).

Patent documents separate claims from embodiments: the claims section states
*what* is protected, the examples section shows *how* it was made. Tagging
chunks with ``claim_no`` / ``example_no`` / ``section_title`` at ingest time
lets retrieval stitch the two together with a plain SQL JOIN instead of an
LLM graph pass (see 集成方案.md §7.1d, §7.4).

Patterns cover Chinese and English patent phrasing. First match wins per
chunk; a chunk can carry both a claim and an example number when the text
discusses both.
"""

from __future__ import annotations

import re

_CLAIM_RES = (
    re.compile(r"权利要求书?\s*第?\s*(\d+)\s*项"),  # 权利要求书第 3 项 / 权利要求 1
    re.compile(r"权利要求\s*(\d+)"),
    re.compile(r"\bclaims?\s*(?:no\.?\s*)?(\d+)", re.IGNORECASE),
)

_EXAMPLE_RES = (
    re.compile(r"实施例\s*(\d+)"),
    re.compile(r"实施方式\s*(\d+)"),
    re.compile(r"\bexamples?\s*(?:no\.?\s*)?(\d+)", re.IGNORECASE),
    re.compile(r"\bembodiments?\s*(?:no\.?\s*)?(\d+)", re.IGNORECASE),
)


def _first_int(patterns: tuple[re.Pattern[str], ...], text: str) -> int | None:
    for rx in patterns:
        m = rx.search(text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def extract_patent_tags(text: str, heading_path: str = "") -> dict:
    """Extract rule-based patent tags from chunk text.

    Returns a dict with any of ``claim_no`` / ``example_no`` (int) and
    ``section_title`` (last heading segment). Empty dict when nothing matches.
    """
    tags: dict = {}
    text = text or ""
    claim_no = _first_int(_CLAIM_RES, text)
    if claim_no is not None:
        tags["claim_no"] = claim_no
    example_no = _first_int(_EXAMPLE_RES, text)
    if example_no is not None:
        tags["example_no"] = example_no
    if heading_path:
        # heading_path is " > "-joined titles; the leaf is the section.
        leaf = heading_path.split(" > ")[-1].strip()
        if leaf:
            tags["section_title"] = leaf[:120]
    return tags
