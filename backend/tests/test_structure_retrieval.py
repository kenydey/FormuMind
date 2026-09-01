"""Structure-image recognition + similarity search tests.

The heavy MolScribe dispatch is monkeypatched (the real container path is
covered by the e2e check) — these tests pin the plumbing: shared-volume file
handling, cache, validation gating, and Tanimoto ranking against a real
material store.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import knowledge
from app.main import app
from app.services.structure_recognize import recognize_structure_image

client = TestClient(app)

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64  # PNG magic + padding


@pytest.fixture()
def material_store(tmp_path, monkeypatch):
    """Isolated material store so similarity hits are deterministic."""
    import app.db.material_store as material_store_mod
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.material_store import MaterialStore

    engine = make_engine(f"sqlite:///{tmp_path}/struct.db")
    Base.metadata.create_all(engine)
    store = MaterialStore(make_session_factory(engine))
    store.seed_missing(knowledge._SEED_MATERIALS)
    monkeypatch.setattr(material_store_mod, "_store", store)
    knowledge.RAW_MATERIALS.refresh()
    yield store
    knowledge.RAW_MATERIALS.refresh()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Keep cache writes out of tests (no real Redis assumptions)."""
    monkeypatch.setattr(
        "app.services.structure_recognize._cache_get", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.structure_recognize._cache_put", lambda *a, **k: None
    )


class TestRecognizePipeline:
    def test_invalid_image_type_rejected(self, material_store):
        res = recognize_structure_image(b"plain text, not an image")
        assert res["recognized"] is False
        assert "仅支持" in (res["error"] or "")

    def test_empty_image_rejected(self):
        res = recognize_structure_image(b"")
        assert res["recognized"] is False
        assert res["error"] == "空图片"

    def test_oversize_rejected(self, monkeypatch):
        # 10 MB limit; send 11 MB of PNG magic + padding
        big = _FAKE_PNG + b"1" * (11 * 1024 * 1024)
        res = recognize_structure_image(big)
        assert res["recognized"] is False
        assert "限制" in (res["error"] or "")

    def test_full_pipeline_success(self, material_store, monkeypatch):
        """MolScribe returns DGEBA-ish SMILES; validation passes; hits found."""
        from rdkit import Chem
        from rdkit.Chem import AllChem, DataStructs

        monkeypatch.setattr(
            "app.worker.celery_app.celery_app.send_task",
            lambda *a, **k: _FakeAsyncResult(
                {"ok": True, "smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"}
            ),
        )
        res = recognize_structure_image(_FAKE_PNG, filename="struct.png")
        assert res["recognized"] is True
        assert res["smiles"] == "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"
        assert res["moljson"] is not None and res["moljson"].get("bonds")
        assert res["image_sha"]  # sha256 of the fake png
        assert isinstance(res["hits"], list)

    def test_molscribe_failure_degrades(self, material_store, monkeypatch):
        monkeypatch.setattr(
            "app.worker.celery_app.celery_app.send_task",
            lambda *a, **k: _FakeAsyncResult(
                {"ok": False, "smiles": None, "reason": "MolScribe unavailable"}
            ),
        )
        res = recognize_structure_image(_FAKE_PNG)
        assert res["recognized"] is False
        assert res["error"] and "识别" in res["error"]
        assert any("聚合物" in w for w in res["warnings"])

    def test_invalid_recognized_smiles_dropped(self, material_store, monkeypatch):
        monkeypatch.setattr(
            "app.worker.celery_app.celery_app.send_task",
            lambda *a, **k: _FakeAsyncResult(
                {"ok": True, "smiles": "not-a-molecule"}
            ),
        )
        res = recognize_structure_image(_FAKE_PNG)
        assert res["recognized"] is False
        assert any("结构校验" in w for w in res["warnings"])


class TestSimilarityHits:
    def test_similarity_hits_rank_by_tanimoto(self, material_store):
        from app.services.structure_search import similarity_hits

        # Ethanol → should find alcohol-ish materials above unrelated ones
        hits = similarity_hits("CCO", top_k=5, threshold=0.0)
        assert isinstance(hits, list)
        for h in hits:
            assert "name" in h and "similarity" in h and 0.0 <= h["similarity"] <= 1.0
        sims = [h["similarity"] for h in hits]
        assert sims == sorted(sims, reverse=True)

    def test_invalid_query_returns_empty(self):
        from app.services.structure_search import similarity_hits

        assert similarity_hits("not-a-molecule") == []
        assert similarity_hits("") == []

    def test_threshold_filters(self, material_store):
        from app.services.structure_search import similarity_hits

        # Threshold 1.0 → only exact self-match could pass; catalog likely has none
        hits = similarity_hits("CCO", top_k=5, threshold=1.0)
        for h in hits:
            assert h["similarity"] >= 1.0

    def test_query_terms_collapse(self):
        from app.services.structure_search import structure_query_terms

        s = structure_query_terms(
            [{"name": "环氧树脂", "role": "resin"}, {"name": "环氧树脂", "role": "x"}]
        )
        assert s.count("环氧树脂") == 1


class TestStructureEndpoint:
    def test_endpoint_rejects_non_image(self, material_store):
        r = client.post(
            "/api/chemical/structure",
            files={"image": ("bad.txt", b"hello world", "text/plain")},
        )
        assert r.status_code == 200  # degrade, not 4xx
        body = r.json()
        assert body["recognized"] is False

    def test_endpoint_missing_image_422(self):
        r = client.post("/api/chemical/structure", data={"threshold": 0.5})
        assert r.status_code == 422


class TestChatStructureContext:
    def test_context_uses_hit_names_only(self):
        from app.domain.chat_schemas import structure_retrieval_context

        s = structure_retrieval_context(
            {
                "smiles": "CCO",
                "moljson": {"atoms": [], "bonds": []},
                "hits": [
                    {"name": "环氧树脂E51", "role": "resin", "similarity": 0.9},
                    {"name": "双酚A", "role": "monomer", "similarity": 0.8},
                    {"name": "环氧树脂E51", "role": "dup", "similarity": 0.9},
                ],
            }
        )
        assert "环氧树脂E51" in s and "双酚A" in s
        assert "CCO" not in s  # SMILES not used as retrieval token
        assert s.count("环氧树脂E51") == 1  # dedup

    def test_empty_structure_returns_empty(self):
        from app.domain.chat_schemas import structure_retrieval_context

        assert structure_retrieval_context(None) == ""
        assert structure_retrieval_context({}) == ""


class _FakeAsyncResult:
    """Minimal stand-in for celery AsyncResult.get()."""

    def __init__(self, payload: dict):
        self._payload = payload

    def get(self, timeout=None):
        return self._payload
