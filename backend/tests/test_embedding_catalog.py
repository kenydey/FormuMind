"""Option A: embedding model catalog + status surface."""
from __future__ import annotations

from app.services import rag


def test_embedding_catalog_includes_qwen_and_defaults():
    catalog = rag.embedding_model_catalog()
    ids = {row["id"] for row in catalog}
    assert "sentence-transformers/all-MiniLM-L6-v2" in ids
    assert "BAAI/bge-small-zh-v1.5" in ids
    assert "Qwen/Qwen3-Embedding-0.6B" in ids
    for row in catalog:
        assert row["id"] and row["label"]


def test_embedding_status_exposes_reindex_hint(monkeypatch):
    fake = type(
        "S",
        (),
        {"embedding_model": "Qwen/Qwen3-Embedding-0.6B"},
    )()
    monkeypatch.setattr("app.config.get_settings", lambda: fake)
    monkeypatch.setattr(rag, "embed_model_name", lambda lang=None: fake.embedding_model)
    status = rag.embedding_status()
    assert status["embedding_model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert status["embedding_model_configured"] == "Qwen/Qwen3-Embedding-0.6B"
    assert "重建" in status["reindex_hint"]
    assert any(r["id"].startswith("Qwen/") for r in status["embedding_catalog"])
