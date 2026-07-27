"""Task 2.6: 答案引用绑定 [^n] — citation formatting, LLM prompt template,
citation extraction, and footnote generation.

Integrates ``CitationAnchor`` (Task 2.3) with LLM answer synthesis so that
generated responses embed footnote-style ``[^n]`` markers that map back to
specific KB chunks with page/paragraph/offset provenance.
"""

from __future__ import annotations

import re
from typing import TypedDict

from ..domain.citations import CitationAnchor


# ── Regex patterns ─────────────────────────────────────────────────────────────

# Matches [^n] markers (1 or more digits) in LLM-generated text.
_CITATION_REF_RE = re.compile(r"\[\^(\d+)\]")

# Matches a single footnote definition line: [^n]: ...
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s", re.MULTILINE)


# ── Typed results ──────────────────────────────────────────────────────────────


class CitationBinding(TypedDict):
    """Result of binding citations into an LLM answer."""

    answer: str
    """The LLM-generated answer text with in-text [^n] markers."""

    footnotes: str
    """Markdown footnote definitions section (one [^n]: ... per cited reference)."""

    used_citations: list[tuple[int, CitationAnchor]]
    """(index, anchor) pairs for every citation referenced in the answer."""


class PromptParts(TypedDict):
    """Components produced by the citation-aware prompt builder."""

    system_instruction: str
    """System-level instructions: role, rules, citation protocol."""

    citation_context: str
    """Numbered citation list for injection into the user prompt."""

    full_prompt: str
    """Complete prompt combining system instructions, citations, and question."""


# ── Formatting: CitationAnchor → [^n] markdown ────────────────────────────────


def format_citation_list(
    anchors: list[CitationAnchor],
    *,
    max_text_len: int = 300,
) -> str:
    """Build a numbered citation reference list for the LLM prompt.

    Each entry is one line::

        [^1] Source: src-id, pp. 3, ¶42 — chunk text preview...

    This is injected into the prompt so the LLM knows which ``[^n]``
    markers correspond to which evidence.

    Parameters
    ----------
    anchors : list[CitationAnchor]
        Ordered list of citations (index 0 → [^1], index 1 → [^2], etc.).
    max_text_len : int
        Truncate each chunk's text preview to this many characters (default 300).
    """
    if not anchors:
        return "_无可引用的文献。_"

    lines: list[str] = []
    for i, a in enumerate(anchors, start=1):
        ref = a.to_reference(i)
        cite = a.to_citation_text()
        preview = (a.text or "")[:max_text_len]
        if len(a.text or "") > max_text_len:
            preview = f"{preview}…"
        lines.append(f"{ref} {cite} — {preview}")

    return "\n".join(lines)


def build_footnotes_section(
    anchors: list[CitationAnchor],
    used_indices: list[int] | None = None,
) -> str:
    """Generate a Markdown footnotes section from the anchor list.

    Produces::

        ---
        [^1]: Source: src-42, pp. 3, ¶5 — preview…
        [^3]: Source: src-99, pp. 12 — preview…

    When *used_indices* is provided (1-based), only those entries are emitted.

    Parameters
    ----------
    anchors : list[CitationAnchor]
        Ordered list.  Index 0 → `[^1]`.
    used_indices : list[int] or None
        1-based indices to include.  When None, all anchors are included.
    """
    if not anchors:
        return ""

    if used_indices is not None:
        used_set = set(used_indices)
        selected = [(i + 1, anchors[i]) for i in range(len(anchors)) if (i + 1) in used_set]
        selected.sort(key=lambda pair: pair[0])
    else:
        selected = [(i + 1, a) for i, a in enumerate(anchors)]

    if not selected:
        return ""

    lines: list[str] = ["", "---", ""]
    for n, a in selected:
        cite = a.to_citation_text()
        preview = (a.text or "")[:200]
        lines.append(f"[^{n}]: {cite} — {preview}")

    return "\n".join(lines)


# ── Prompt template ───────────────────────────────────────────────────────────


def build_citation_prompt(
    question: str,
    anchors: list[CitationAnchor],
    *,
    domain: str | None = None,
    system_instruction: str | None = None,
) -> str:
    """Build a citation-aware LLM prompt with numbered reference list.

    The prompt instructs the LLM to:

    1. Answer using ONLY the provided sources.
    2. Embed ``[^n]`` markers after every factual claim.
    3. Never cite evidence not present in the reference list.
    4. State 证据不足 (insufficient evidence) explicitly when sources don't cover
       a claim.

    Parameters
    ----------
    question : str
        The user's research question.
    anchors : list[CitationAnchor]
        Ordered citation anchors from retrieved chunks.
    domain : str or None
        Optional domain context (e.g., "防腐蚀涂料").
    system_instruction : str or None
        Override the default system instruction.
    """
    cite_list = format_citation_list(anchors)

    if system_instruction is None:
        system_lines = [
            "你是一位资深配方研究员（formulation scientist）。",
            "请严格遵循以下引用协议回答用户问题：",
            "",
            "**引用规则**",
            f"1. 每条技术论断后插入对应的引用标记 [^n]（n 为下方引用列表中方括号内的编号）。",
            "2. 只能引用下方提供的文献——不得编造、假设或引用不存在的证据。",
            "3. 一个论断如果有多条文献支持，可写 [^1][^3]。",
            "4. 来源数据冲突时必须指出冲突，并说明取舍理由。",
            "5. 缺乏证据支撑的地方务必写「证据不足」——宁可保守，不可编造。",
            "6. 用简体中文回答，Markdown 格式，保持表格、化学式、平衡式完整。",
        ]
        system = "\n".join(system_lines)
    else:
        system = system_instruction

    domain_hint = f"\n领域：{domain}\n" if domain else ""

    return (
        f"{system}\n\n"
        f"**可引用文献**（严格按照编号引用）：\n\n"
        f"{cite_list}\n\n"
        f"{domain_hint}"
        f"**问题**：{question}\n\n"
        "请严格遵循上述引用规则回答："
    )


# ── Extraction ────────────────────────────────────────────────────────────────


def extract_citation_indices(text: str) -> list[int]:
    """Extract all unique citation reference numbers from an LLM answer.

    Finds every ``[^n]`` marker in *text* and returns the sorted,
    deduplicated list of numeric indices.

    Parameters
    ----------
    text : str
        LLM-generated answer text potentially containing ``[^1]``, etc.

    Returns
    -------
    list[int]
        Sorted unique 1-based citation indices (e.g., ``[1, 2, 5]``).
    """
    raw = _CITATION_REF_RE.findall(text)
    seen: set[int] = set()
    indices: list[int] = []
    for s in raw:
        n = int(s)
        if n > 0 and n not in seen:
            seen.add(n)
            indices.append(n)
    indices.sort()
    return indices


def extract_citation_indices_validated(
    text: str,
    total_anchors: int,
) -> tuple[list[int], list[int]]:
    """Extract citation indices and separate valid vs out-of-range.

    Returns
    -------
    (valid_indices, out_of_range)
        *valid_indices* are within [1, total_anchors].
        *out_of_range* are indices that exceed total_anchors (likely LLM
        hallucination — the model cited a reference number that doesn't exist).
    """
    all_indices = extract_citation_indices(text)
    valid: list[int] = []
    out_of_range: list[int] = []
    for n in all_indices:
        if 1 <= n <= total_anchors:
            valid.append(n)
        else:
            out_of_range.append(n)
    return valid, out_of_range


def has_citation_markers(text: str) -> bool:
    """Return True if *text* contains any ``[^n]`` markers."""
    return bool(_CITATION_REF_RE.search(text))


def strip_existing_footnotes(text: str) -> str:
    """Remove any pre-existing ``[^n]: …`` footnote definition lines from *text*.

    Useful when merging LLM output with a fresh footnotes section to avoid
    duplication.
    """
    return _FOOTNOTE_DEF_RE.sub("", text).rstrip()


# ── Binding: full pipeline ────────────────────────────────────────────────────


def bind_citations(
    answer: str,
    anchors: list[CitationAnchor],
) -> CitationBinding:
    """Post-process an LLM answer: extract used citations + append footnotes.

    Parameters
    ----------
    answer : str
        Raw LLM answer that (should) contain ``[^n]`` markers.
    anchors : list[CitationAnchor]
        Ordered citation anchors (index 0 → ``[^1]``).

    Returns
    -------
    CitationBinding
        Contains the cleaned answer, footnotes section, and list of
        (index, anchor) pairs for each cited reference.
    """
    used_indices = extract_citation_indices(answer)
    used_pairs: list[tuple[int, CitationAnchor]] = []
    for n in used_indices:
        idx = n - 1
        if 0 <= idx < len(anchors):
            used_pairs.append((n, anchors[idx]))

    # Strip any footnotes the LLM might have generated itself.
    clean_answer = strip_existing_footnotes(answer)

    footnotes = build_footnotes_section(anchors, used_indices)

    return CitationBinding(
        answer=clean_answer,
        footnotes=footnotes,
        used_citations=used_pairs,
    )


def format_answer_with_citations(
    answer: str,
    anchors: list[CitationAnchor],
) -> str:
    """Convenience: return answer + footnotes as a single markdown string."""
    binding = bind_citations(answer, anchors)
    return f"{binding['answer']}\n{binding['footnotes']}"


__all__ = [
    "CitationBinding",
    "PromptParts",
    "build_citation_prompt",
    "build_footnotes_section",
    "format_citation_list",
    "extract_citation_indices",
    "extract_citation_indices_validated",
    "has_citation_markers",
    "strip_existing_footnotes",
    "bind_citations",
    "format_answer_with_citations",
]
