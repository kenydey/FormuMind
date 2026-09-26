"""P2: multilingual embedding unifies bilingual search (no translate needed)."""
from __future__ import annotations

from app.services import kb_bilingual


def test_unified_space_skips_translate(monkeypatch):
    log: dict = {"calls": []}

    def fake_retrieve(query, k=6, project_id=None, mode="hybrid", langs=None, **kwargs):
        log["calls"].append((query, mode, langs))
        from app.domain.schemas import Evidence

        return [
            Evidence(
                source="t",
                identifier=f"u-{i}",
                title="t",
                snippet="s",
                relevance=1.0,
            )
            for i in range(2)
        ]

    monkeypatch.setattr("app.services.kb_index.retrieve_evidence", fake_retrieve)
    monkeypatch.setattr(
        "app.services.rag.embedding_space_unified", lambda: True
    )

    class _S:
        kb_bilingual = True
        kb_query_translate = True

    out = kb_bilingual.search("如何提高耐盐雾", k=5, settings=_S())
    assert out
    # Unified space → single hybrid call; no translated en query / langs filter.
    assert log["calls"] == [("如何提高耐盐雾", "hybrid", None)]


def test_dual_space_still_translates(monkeypatch):
    log: dict = {"calls": []}

    def fake_retrieve(query, k=6, project_id=None, mode="hybrid", langs=None, **kwargs):
        log["calls"].append((query, langs, mode))
        from app.domain.schemas import Evidence

        tag = (langs or ["x"])[0]
        return [
            Evidence(
                source="t",
                identifier=f"{tag}-{i}",
                title="t",
                snippet="s",
                relevance=1.0,
            )
            for i in range(2)
        ]

    monkeypatch.setattr("app.services.kb_index.retrieve_evidence", fake_retrieve)
    monkeypatch.setattr(
        "app.services.rag.embedding_space_unified", lambda: False
    )
    monkeypatch.setattr(
        "app.services.query_translate.translate_query_zh_to_en",
        lambda q: "salt spray resistance",
    )

    class _S:
        kb_bilingual = True
        kb_query_translate = True

    kb_bilingual.search("如何提高耐盐雾", k=5, settings=_S())
    assert len(log["calls"]) == 2
    assert log["calls"][0][1] == ["zh"]
    assert log["calls"][1] == ("salt spray resistance", ["en"], "legacy")


def test_embedding_space_unified_catalog(monkeypatch):
    from app.config import get_settings
    from app.services import rag

    monkeypatch.setenv("FORMUMIND_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    get_settings.cache_clear()
    try:
        assert rag.embedding_space_unified() is True
        status = rag.embedding_status()
        assert status["embedding_space_unified"] is True
    finally:
        get_settings.cache_clear()


def test_embedding_space_not_unified_when_unset(monkeypatch):
    from app.config import get_settings
    from app.services import rag

    monkeypatch.delenv("FORMUMIND_EMBEDDING_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        assert rag.embedding_space_unified() is False
    finally:
        get_settings.cache_clear()
