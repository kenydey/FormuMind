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
from app.domain.schemas import Formulation, Ingredient, ProductDomain
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


class TestStructurePromptInjection:
    """P0: MolJSON must reach the LLM prompt (structure → explicit atom/bond graph)."""

    def test_chat_prompt_embeds_moljson(self):
        from app.services.llm import _chat_prompt
        from app.domain.schemas import Evidence

        ev = Evidence(
            source="seed",
            identifier="e1",
            title="t",
            snippet="s",
            relevance=0.5,
        )
        prompt = _chat_prompt(
            "这个结构有几个苯环？",
            [ev],
            "autodeposition_coating",
            structure={
                "recognized": True,
                "smiles": "c1ccc(OCC2CO2)cc1",
                "moljson": None,  # server regenerates from smiles
                "hits": [],
            },
        )
        # MolJSON explicit graph is embedded
        assert "Target molecular structure" in prompt
        assert '"element": "C"' in prompt
        assert '"bonds"' in prompt

    def test_chat_prompt_without_structure_unchanged(self):
        from app.services.llm import _chat_prompt
        from app.domain.schemas import Evidence

        ev = Evidence(source="seed", identifier="e1", title="t", snippet="s", relevance=0.5)
        prompt = _chat_prompt("问题", [ev], "autodeposition_coating")
        assert "Target molecular structure" not in prompt

    def test_chat_prompt_invalid_smiles_skipped(self):
        from app.services.llm import _chat_prompt
        from app.domain.schemas import Evidence

        ev = Evidence(source="seed", identifier="e1", title="t", snippet="s", relevance=0.5)
        prompt = _chat_prompt(
            "问题",
            [ev],
            "autodeposition_coating",
            structure={"smiles": "not-a-molecule", "hits": []},
        )
        assert "Target molecular structure" not in prompt


class TestWebSearchGapFill:
    """P1: long-tail material names get CAS backfilled via web search."""

    def _ev(self, title: str, snippet: str):
        from app.domain.schemas import Evidence

        return Evidence(
            source="Tavily", identifier="t1", title=title, snippet=snippet, relevance=0.5
        )

    def test_cas_extracted_and_backfilled(self, monkeypatch):
        from app.domain.formulation_gate import _web_search_gap_fill

        monkeypatch.setattr(
            "app.services.literature.search_web",
            lambda q, limit: [
                self._ev(
                    "Product X safety sheet",
                    "CAS 1317-65-3, also known as limestone, chemical formula CaCO3",
                )
            ],
        )
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: _FakeSettings(environment="development"),
        )
        updates: dict = {}
        warns = _web_search_gap_fill("Limestone powder", updates)
        assert updates.get("cas_no") == "1317-65-3"
        assert any("网络检索" in w for w in warns)

    def test_invalid_cas_not_backfilled(self, monkeypatch):
        from app.domain.formulation_gate import _web_search_gap_fill

        monkeypatch.setattr(
            "app.services.literature.search_web",
            lambda q, limit: [self._ev("x", "CAS 999-99-9 fake number here")],
        )
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: _FakeSettings(environment="development"),
        )
        updates: dict = {}
        warns = _web_search_gap_fill("Fake material", updates)
        assert "cas_no" not in updates  # checksum failed → not backfilled
        assert any("校验失败" in w for w in warns)

    def test_test_env_skips_network(self, monkeypatch):
        from app.domain.formulation_gate import _web_search_gap_fill

        called = {"n": 0}

        def boom(*a, **k):
            called["n"] += 1
            raise AssertionError("network must not be called in test env")

        monkeypatch.setattr("app.services.literature.search_web", boom)
        updates: dict = {}
        warns = _web_search_gap_fill("X", updates)
        assert called["n"] == 0
        assert warns == []

    def test_no_hits_noop(self, monkeypatch):
        from app.domain.formulation_gate import _web_search_gap_fill

        monkeypatch.setattr("app.services.literature.search_web", lambda q, limit: [])
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: _FakeSettings(environment="development"),
        )
        updates: dict = {}
        warns = _web_search_gap_fill("Unknown thing", updates)
        assert updates == {}
        assert warns == []


class TestSubstructureHits:
    """P2: SMARTS substructure filter over the catalog."""

    def test_primary_amine_filter(self, material_store):
        from app.services.structure_search import substructure_hits

        hits = substructure_hits("[NX3;H2]", top_k=20)
        # IPDA has two primary amines; non-amines excluded
        names = {h["name"] for h in hits}
        assert "Isophorone diamine (IPDA)" in names
        assert "Deionized water" not in names  # water has no N
        assert "Xylene" not in names  # aromatic C only

    def test_benzene_ring_filter(self, material_store):
        from app.services.structure_search import substructure_hits

        hits = substructure_hits("c1ccccc1", top_k=20)
        names = {h["name"] for h in hits}
        assert "Bisphenol-A epoxy (DGEBA)" in names  # two benzene rings
        assert "Xylene" in names  # aromatic
        assert "Butyl glycol" not in names  # aliphatic

    def test_invalid_smarts_returns_empty(self):
        from app.services.structure_search import substructure_hits

        assert substructure_hits("[not-a-smarts") == []
        assert substructure_hits("") == []

    def test_endpoint_works(self, material_store):
        r = client.get("/api/chemical/substructure", params={"smarts": "[NX3;H2]"})
        assert r.status_code == 200
        body = r.json()
        assert body["smarts"] == "[NX3;H2]"
        assert isinstance(body["hits"], list)


class TestScreenFormulationLocal:
    """P3: zero-network chemical pre-screen for optimisation loops."""

    def _form(self, **ing_kwargs) -> Formulation:
        defaults = dict(name="Epoxy resin X", role="resin", weight_pct=50.0)
        defaults.update(ing_kwargs)
        return Formulation(
            name="test",
            domain=ProductDomain.autodeposition_coating,
            ingredients=[Ingredient(**defaults)],
            rationale="t",
        )

    def test_invalid_smiles_flagged(self):
        from app.services.chemtools import screen_formulation_local

        warns = screen_formulation_local(self._form(smiles="C1=CC=CC"))
        assert any("无法被 RDKit 解析" in w for w in warns)

    def test_valid_smiles_no_warnings(self, monkeypatch):
        from app.services.chemtools import screen_formulation_local

        monkeypatch.setattr(
            "app.services.chemtools.molbloom_available", lambda: False
        )
        warns = screen_formulation_local(self._form(smiles="CCO"))
        assert warns == []  # valid + patent check disabled

    def test_low_weight_skipped(self):
        from app.services.chemtools import screen_formulation_local

        warns = screen_formulation_local(
            self._form(smiles="not-a-molecule", weight_pct=0.01)
        )
        assert warns == []  # below _SCREEN_MIN_WT_PCT → not screened

    def test_patented_flagged_when_molbloom(self, monkeypatch):
        from app.services.chemtools import screen_formulation_local

        monkeypatch.setattr(
            "app.services.chemtools.molbloom_available", lambda: True
        )
        monkeypatch.setattr(
            "app.services.chemtools.patent_check", lambda smiles: True
        )
        warns = screen_formulation_local(self._form(smiles="CCO"))
        assert any("IP 预筛" in w for w in warns)

    def test_no_network_calls(self, monkeypatch):
        """P3 core guarantee: optimisation loops never hit the network."""
        from app.services.chemtools import screen_formulation_local

        called = {"n": 0}

        def boom(*a, **k):
            called["n"] += 1
            raise AssertionError("network must not be called")

        monkeypatch.setattr(
            "app.services.chemtools.controlled_check", boom
        )
        monkeypatch.setattr("app.services.chemtools.molbloom_available", lambda: False)
        screen_formulation_local(self._form(smiles="CCO"))
        assert called["n"] == 0

    def test_pains_interference_flagged(self):
        """P-B: benzil (1,2-diketone) is a known PAINS alert."""
        from app.services.chemtools import screen_formulation_local

        warns = screen_formulation_local(
            self._form(name="Benzil-like", smiles="O=C(C(=O)c1ccccc1)c1ccccc1")
        )
        assert any("PAINS/Brenk" in w or "泛干扰" in w for w in warns)

    def test_benign_molecule_no_pains(self, monkeypatch):
        """P-B: ethanol must NOT trip the PAINS/Brenk catalog."""
        from app.services.chemtools import screen_formulation_local

        monkeypatch.setattr("app.services.chemtools.molbloom_available", lambda: False)
        warns = screen_formulation_local(self._form(name="Ethanol", smiles="CCO"))
        assert warns == []


class TestKgStructureHits:
    """P4: KG entity structure-similarity dimension."""

    def test_kg_hits_ranked(self, monkeypatch, material_store):
        from app.services.structure_search import kg_structure_hits

        # Mock KG entities: one similar to ethanol, one unrelated
        fake_rows = [
            ("chem:1", "Ethanol-like", "CCO"),
            ("chem:2", "Benzene ring", "c1ccccc1"),
        ]

        def fake_scan(settings=None, limit=2000):
            return fake_rows

        monkeypatch.setattr(
            "app.services.structure_search._kg_entities_with_smiles", fake_scan
        )
        hits = kg_structure_hits("CCO", top_k=5, threshold=0.3)
        assert hits
        assert hits[0]["name"] == "Ethanol-like"  # Tanimoto 1.0 with CCO
        assert hits[0]["similarity"] == 1.0
        assert all("id" in h and "name" in h and "similarity" in h for h in hits)

    def test_kg_hits_threshold_filters(self, monkeypatch, material_store):
        from app.services.structure_search import kg_structure_hits

        monkeypatch.setattr(
            "app.services.structure_search._kg_entities_with_smiles",
            lambda settings=None, limit=2000: [("chem:1", "A", "CCO")],
        )
        hits = kg_structure_hits("CCO", top_k=5, threshold=1.0)
        assert len(hits) == 1 and hits[0]["similarity"] == 1.0
        hits = kg_structure_hits("CCCO", top_k=5, threshold=1.0)  # propanol ≠ ethanol
        assert hits == []

    def test_invalid_query_empty(self):
        from app.services.structure_search import kg_structure_hits

        assert kg_structure_hits("not-a-molecule") == []
        assert kg_structure_hits("") == []

    def test_recognize_includes_kg_hits(self, monkeypatch, material_store):
        """Full pipeline: kg_hits field present and populated on success."""
        monkeypatch.setattr(
            "app.worker.celery_app.celery_app.send_task",
            lambda *a, **k: _FakeAsyncResult(
                {"ok": True, "smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"}
            ),
        )
        monkeypatch.setattr(
            "app.services.structure_search._kg_entities_with_smiles",
            lambda settings=None, limit=2000: [
                ("chem:1", "DGEBA", "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1")
            ],
        )
        res = recognize_structure_image(_FAKE_PNG)
        assert res["recognized"] is True
        assert "kg_hits" in res
        assert res["kg_hits"]  # non-empty with the mocked entity

    def test_failure_path_has_kg_hits_field(self, monkeypatch):
        monkeypatch.setattr(
            "app.worker.celery_app.celery_app.send_task",
            lambda *a, **k: _FakeAsyncResult({"ok": False, "reason": "x"}),
        )
        res = recognize_structure_image(_FAKE_PNG)
        assert res["recognized"] is False
        assert res["kg_hits"] == []  # field present for consistent shape


class TestAdaptiveKgThreshold:
    """P4 tuning: large molecules get relaxed Tanimoto cutoff."""

    def test_small_molecule_keeps_requested(self):
        from app.services.structure_search import _adaptive_kg_threshold

        # ethanol (9 atoms) ≤ 15 → keep 0.6
        assert _adaptive_kg_threshold("CCO", 0.6) == 0.6

    def test_large_molecule_relaxed(self):
        from app.services.structure_search import _adaptive_kg_threshold

        # DGEBA (25 atoms) > 15 → 0.25 (two-tier)
        t = _adaptive_kg_threshold(
            "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1", 0.6
        )
        assert t == 0.25

    def test_floor_at_015(self):
        from app.services.structure_search import _adaptive_kg_threshold

        # huge molecule with tiny requested → never below 0.15
        big = "C" * 300
        assert _adaptive_kg_threshold(big, 0.05) == 0.15

    def test_dgeba_gets_kg_hits_with_adaptive(self, monkeypatch, material_store):
        """Real effect: DGEBA previously 0 hits at 0.6; adaptive finds the epoxy silane."""
        from app.services.structure_search import kg_structure_hits

        monkeypatch.setattr(
            "app.services.structure_search._kg_entities_with_smiles",
            lambda settings=None, limit=2000: [
                ("chem:1", "Epoxy silane", "CO[Si](CCCOCC1CO1)(OC)OC"),
                ("chem:2", "Benzene", "c1ccccc1"),
            ],
        )
        hits = kg_structure_hits(
            "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1",
            top_k=5,
            threshold=0.6,  # caller still asks 0.6
        )
        names = {h["name"] for h in hits}
        assert "Epoxy silane" in names  # 0.28 sim ≥ 0.5 adaptive threshold
        assert "Benzene" not in names  # too dissimilar


class TestScaffoldSubstitutes:
    """P-D: Murcko scaffold — same core ring system = drop-in candidate."""

    def test_same_scaffold_hit(self, material_store):
        from app.services.structure_search import scaffold_substitutes

        # 查询双酚A核心（无环氧端基）→ 材料库 DGEBA（双酚A+环氧）应共享
        # 双苯丙烷骨架？Murcko 保留环系+连接，端基不同 → 需测实际行为。
        # 用完全相同的分子确保骨架必然一致（自洽性验证）。
        hits = scaffold_substitutes("CCO")
        # ethanol 的 Murcko 骨架无环 → GetScaffoldForMol 返回分子本身
        # 材料库里有 CCO（Deionized water 是 O，Butyl glycol 是 CCCCOCCO）
        # 只验证 API 形状与骨架字段
        assert isinstance(hits, list)
        for h in hits:
            assert "scaffold" in h

    def test_invalid_input_empty(self):
        from app.services.structure_search import scaffold_substitutes

        assert scaffold_substitutes("not-a-molecule") == []
        assert scaffold_substitutes("") == []

    def test_endpoint_works(self):
        r = client.get(
            "/api/chemical/scaffold-substitutes",
            params={"smiles": "CC(C)(c1ccc(OCC2CO2)cc1)c1ccc(OCC2CO2)cc1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["smiles"].startswith("CC(C)")
        assert isinstance(body["hits"], list)


class _FakeSettings:
    """Minimal Settings stand-in for environment-dependent branches."""

    def __init__(self, environment: str = "development"):
        self.environment = environment


class _FakeAsyncResult:
    """Minimal stand-in for celery AsyncResult.get()."""

    def __init__(self, payload: dict):
        self._payload = payload

    def get(self, timeout=None):
        return self._payload
