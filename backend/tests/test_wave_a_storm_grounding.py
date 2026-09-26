"""Wave A: STORM evidence thickening, page anchors, pack_to_evidence quality."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services.wiki import storm_draft, storm_orchestrator, storm_polish
from app.services.wiki.storm_schema import SectionSpec


def test_collect_section_evidence_uses_retrieve_and_cap(monkeypatch):
    calls: list[dict] = []

    def fake_retrieve(q, k=6, **kwargs):
        calls.append({"q": q, "k": k, "mode": kwargs.get("mode")})
        return [
            Evidence(
                source="kb",
                identifier=f"kb:s{i}#c0",
                title=f"Doc{i}",
                snippet="epoxy coating salt spray 720",
                relevance=0.8,
                page=3 + i,
            )
            for i in range(k)
        ]

    monkeypatch.setattr("app.services.kb_index.kb_enabled", lambda: True)
    monkeypatch.setattr("app.services.kb_index.retrieve_evidence", fake_retrieve)
    monkeypatch.setenv("FORMUMIND_WIKI_STORM_SECTION_QUERY_K", "6")
    monkeypatch.setenv("FORMUMIND_WIKI_STORM_SECTION_EVIDENCE_CAP", "10")
    from app.config import get_settings

    get_settings.cache_clear()

    spec = SectionSpec(
        section_id="sec_lit",
        title="文献",
        core_intent="x",
        retrieval_queries=["盐雾 环氧", "硅烷 偶联"],
    )
    pack = {"literature": {"rows": []}}
    try:
        hits = storm_draft.collect_section_evidence(spec, pack, project_id="p1")
    finally:
        get_settings.cache_clear()

    assert calls
    assert all(c["mode"] == "hybrid" for c in calls)
    assert all(c["k"] == 6 for c in calls)
    assert 1 <= len(hits) <= 10
    assert hits[0].get("page")


def test_anchors_include_page_from_lit_and_chunks(monkeypatch):
    pack = {
        "literature": {
            "rows": [
                {
                    "source_id": "src-a",
                    "title": "Paper A",
                    "snippet": "salt spray",
                    "page": 7,
                }
            ]
        }
    }
    anchors = storm_polish._anchors_from_source_ids(["src-a"], pack)
    assert anchors and anchors[0].page == 7
    assert "pp. 7" in anchors[0].to_citation_text()


def test_pack_to_evidence_uses_real_relevance_and_snippet():
    pack = {
        "sources": [
            {
                "source_id": "s1",
                "title": "Doc",
                "snippet": "full excerpt about epoxy crosslinking",
                "relevance": 0.91,
                "page": 2,
            }
        ]
    }
    ev = storm_orchestrator._pack_to_evidence(pack, ["s1"])
    assert len(ev) == 1
    assert ev[0].relevance == 0.91
    assert "crosslinking" in ev[0].snippet
    assert ev[0].page == 2
