"""Response envelopes the frontend wrappers unwrap (round-4) — the Python half of
``frontend/src/api/methods.responseEnvelopes.test.ts``.

``substructureSearch`` / ``scaffoldSubstitutes`` / ``kbChunksBySource`` declared bare arrays on the client while
these endpoints answer ``{…, hits}`` / ``{chunks}``. Component tests mocked the wrapper with arrays, so nothing
noticed until a real structure search crashed the Materials panel. If one of these shapes changes, the wrapper
tests must change with it — this keeps the two sides honest.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/env.db")
    get_settings.cache_clear()
    from app.db.database import Base, default_engine

    Base.metadata.create_all(default_engine())
    yield TestClient(app)
    get_settings.cache_clear()


def test_substructure_search_answers_an_envelope_with_hits(client):
    body = client.get("/api/chemical/substructure", params={"smarts": "[NX3;H2]", "top_k": 5}).json()
    assert body["smarts"] == "[NX3;H2]"
    assert isinstance(body["hits"], list)


def test_scaffold_substitutes_answers_an_envelope_with_hits(client):
    body = client.get("/api/chemical/scaffold-substitutes", params={"smiles": "CC(C)(c1ccc(O)cc1)c1ccc(O)cc1"}).json()
    assert body["smiles"] == "CC(C)(c1ccc(O)cc1)c1ccc(O)cc1"
    assert isinstance(body["hits"], list)


def test_chunks_by_source_answers_a_chunks_envelope(client):
    body = client.get("/api/kb/chunks/by-source/does-not-exist", params={"limit": 10, "offset": 0}).json()
    assert body == {"chunks": []}


def test_the_chunk_fields_the_viewer_reads_are_the_ones_the_endpoint_sends():
    from app.domain.schemas import DocumentChunkResponse

    fields = set(DocumentChunkResponse.model_fields)
    # SourceDetailModal reads these (frontend/src/api/types.ts KbChunk)
    assert {"id", "source_id", "ord", "text", "page", "paragraph", "offset_start", "heading_path"} <= fields
