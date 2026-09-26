"""P1 wave: citation page anchors, HTML table atomic chunks, STORM claim wire."""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.schemas import Evidence
from app.services import chunking
from app.services.kb_index import _chunk_to_evidence
from app.services.llm import _build_context
from app.services.wiki import storm_orchestrator as storm


def test_build_context_includes_page_paragraph_anchors():
    ev = [
        Evidence(
            source="kb",
            identifier="kb:s1#c0",
            title="Doc",
            snippet="epoxy coating salt spray",
            relevance=0.9,
            page=3,
            paragraph=2,
        )
    ]
    ctx = _build_context(ev, max_chars=2000)
    assert "[^1] (p.3, ¶2)" in ctx


def test_chunk_to_evidence_carries_page():
    chunk = SimpleNamespace(
        source_id="src1",
        ord=0,
        heading_path="",
        page_no=7,
        paragraph_idx=1,
        text="hello table row",
    )
    ev = _chunk_to_evidence(chunk, {"src1": {"title": "T", "source_kind": "pdf"}}, 0.8)
    assert ev.page == 7
    assert ev.paragraph == 1


def test_html_table_is_atomic():
    assert chunking._is_atomic("<table><tr><td>a</td></tr></table>") is True
    assert chunking._is_atomic("| a | b |\n|---|---|\n| 1 | 2 |") is True
    assert chunking._is_atomic("plain paragraph about epoxy") is False


def test_html_table_not_split_on_blank_lines():
    md = (
        "# Title\n\n"
        "<table>\n"
        "<tr><td>resin</td><td>30</td></tr>\n"
        "\n"
        "<tr><td>hardener</td><td>10</td></tr>\n"
        "</table>\n\n"
        "After table."
    )
    chunks = chunking.chunk_markdown(md, max_chars=1600, overlap=0)
    joined = "\n".join(c.text for c in chunks)
    assert "<table>" in joined
    assert "hardener" in joined


def test_storm_claim_check_fail_open(monkeypatch):
    pack = {"sources": [{"source_id": "s1", "title": "Paper", "snippet": "salt spray 720h"}]}
    md = "# Report\n\nThe coating lasts 720 hours in salt spray.\n"
    meta = storm._storm_claim_check("topic", md, pack, ["s1"])
    assert meta["applied"] is True
    assert "markdown" in meta
    assert isinstance(meta["pass_rate"], float)


def test_pack_to_evidence_fallback():
    ev = storm._pack_to_evidence({"summary": "dossier summary"}, [])
    assert len(ev) == 1
    assert ev[0].source == "dossier"
