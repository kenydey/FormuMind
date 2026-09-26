"""P2: chat prompt / context share [^n] with citation_binder + STORM."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services.llm import _build_context, _chat_prompt
from app.services.wiki import storm_draft


def test_build_context_uses_footnote_markers():
    ev = [
        Evidence(
            source="kb",
            identifier="kb:s1#c0",
            title="Doc",
            snippet="epoxy coating",
            relevance=0.9,
        )
    ]
    ctx = _build_context(ev, max_chars=2000)
    assert "[^1]" in ctx
    assert "[1] (" not in ctx


def test_chat_prompt_instructs_footnote_citations():
    prompt = _chat_prompt(
        "盐雾？",
        [Evidence(source="kb", identifier="x", title="t", snippet="s", relevance=0.9)],
        None,
    )
    assert "[^1]" in prompt
    assert "citation_binder" in prompt or "[^2]" in prompt
    assert "Cite sources by number [1], [2]" not in prompt


def test_storm_draft_imports_build_citation_prompt():
    import inspect

    src = inspect.getsource(storm_draft.draft_section)
    assert "build_citation_prompt" in src
