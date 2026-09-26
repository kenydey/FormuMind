"""Wave A: retrieve_evidence façade routes hybrid by default."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.services import kb_bilingual, kb_index


def test_retrieve_evidence_default_hybrid(monkeypatch):
    calls: list[str] = []

    def fake_hybrid(query, k=4, **kwargs):
        calls.append("hybrid")
        return [
            Evidence(
                source="kb",
                identifier="kb:s1#c0",
                title="t",
                snippet="salt spray",
                relevance=0.9,
            )
        ]

    def fake_chunks(query, k=6, **kwargs):
        calls.append("chunks")
        return []

    monkeypatch.setattr(kb_index, "kb_enabled", lambda: True)
    monkeypatch.setattr(kb_index, "search_chunks_hybrid", fake_hybrid)
    monkeypatch.setattr(kb_index, "search_chunks", fake_chunks)

    out = kb_index.retrieve_evidence("epoxy", k=3, project_id="p1")
    assert len(out) == 1
    assert calls == ["hybrid"]


def test_retrieve_evidence_langs_forces_legacy(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(kb_index, "kb_enabled", lambda: True)
    monkeypatch.setattr(
        kb_index,
        "search_chunks_hybrid",
        lambda *a, **k: calls.append("hybrid") or [],
    )
    monkeypatch.setattr(
        kb_index,
        "search_chunks",
        lambda *a, **k: calls.append("chunks")
        or [
            Evidence(
                source="kb",
                identifier="kb:s1#c0",
                title="t",
                snippet="x",
                relevance=0.5,
            )
        ],
    )

    out = kb_index.retrieve_evidence("问", k=2, langs=["zh"])
    assert out and calls == ["chunks"]


def test_kb_bilingual_uses_hybrid_when_off(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("FORMUMIND_KB_BILINGUAL", "false")
    get_settings.cache_clear()
    seen: list[str] = []

    def fake_retrieve(q, k=6, **kwargs):
        seen.append(str(kwargs.get("mode") or ""))
        return []

    monkeypatch.setattr(kb_index, "retrieve_evidence", fake_retrieve)
    try:
        kb_bilingual.search("salt spray", k=4, project_id="p")
    finally:
        get_settings.cache_clear()
    assert "hybrid" in seen
