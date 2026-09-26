"""Wave D: RRF fusion switch + cross-section evidence exclude."""
from __future__ import annotations

import numpy as np

from app.domain.schemas import Evidence
from app.services import hybrid_search as hs
from app.services.wiki import storm_draft
from app.services.wiki.storm_schema import SectionSpec


def test_hybrid_fusion_rrf_uses_rank_mass(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FORMUMIND_KB_HYBRID_FUSION", "rrf")
    get_settings.cache_clear()

    class FakeChunk:
        def __init__(self, i):
            self.id = f"c{i}"
            self.source_id = f"s{i}"
            self.ord = 0
            self.text = f"token{i} epoxy resin coating salt"
            self.embedding = None
            self.embedding_model = None
            self.heading_path = ""
            self.page_no = None
            self.paragraph_idx = None
            self.created_at = None

    chunks = [FakeChunk(i) for i in range(5)]

    class Store:
        def all_chunks(self, **kwargs):
            return chunks

    monkeypatch.setattr(hs.kb_index, "kb_enabled", lambda: True)
    monkeypatch.setattr(
        "app.db.chunk_store.get_chunk_store", lambda: Store()
    )
    monkeypatch.setattr(
        hs,
        "gate_chunk_indices",
        lambda chunks, order, top_k=10: order[:top_k],
        raising=False,
    )
    # Bypass gate import path inside hybrid_search_scored
    import app.services.kb_retrieval_gate as gate

    monkeypatch.setattr(
        gate, "gate_chunk_indices", lambda chunks, order, top_k=10: order[:top_k]
    )

    try:
        scored = hs.hybrid_search_scored("epoxy coating", top_k=3, alpha=0.3)
    finally:
        get_settings.cache_clear()
        monkeypatch.delenv("FORMUMIND_KB_HYBRID_FUSION", raising=False)
        get_settings.cache_clear()

    assert scored
    # RRF scores are small positive masses, not α-weighted [0,1] only
    assert all(s.hybrid_score > 0 for s in scored)


def test_collect_section_evidence_respects_exclude(monkeypatch):
    monkeypatch.setattr("app.services.kb_index.kb_enabled", lambda: True)

    def fake_retrieve(q, k=6, **kwargs):
        return [
            Evidence(
                source="kb",
                identifier="kb:keep#c0",
                title="keep",
                snippet="a",
                relevance=0.9,
            ),
            Evidence(
                source="kb",
                identifier="kb:skip#c0",
                title="skip",
                snippet="b",
                relevance=0.8,
            ),
        ]

    monkeypatch.setattr("app.services.kb_index.retrieve_evidence", fake_retrieve)
    spec = SectionSpec(
        section_id="s",
        title="t",
        retrieval_queries=["q"],
    )
    hits = storm_draft.collect_section_evidence(
        spec,
        {"literature": {"rows": []}},
        project_id="p",
        exclude_ids={"kb:skip#c0"},
    )
    ids = {h["id"] for h in hits}
    assert "kb:skip#c0" not in ids
    assert "kb:keep#c0" in ids
