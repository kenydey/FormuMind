"""KB stream P2 — product registry, chunk meta wiring, vision extraction."""
from __future__ import annotations

import json
import sys
import types

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import ProductMention, SourceGuideSchema


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.product_store as product_store_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.product_store import ProductStore
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(source_store_mod, "_store", SourceStore(factory))
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))
    monkeypatch.setattr(product_store_mod, "_store", ProductStore(factory))
    return (
        source_store_mod._store,
        chunk_store_mod._store,
        product_store_mod._store,
    )


# ── product store ────────────────────────────────────────────────────────────


def test_upsert_merges_and_fills_blanks(stores):
    _, _, products = stores
    products.upsert_mentions("s1", [{"trade_name": "Epon", "grade": "828"}], link_structures=False)
    products.upsert_mentions(
        "s2",
        [{"trade_name": "EPON", "grade": "828", "supplier": "Hexion",
          "generic_name": "双酚A环氧树脂", "role": "树脂"}],
        link_structures=False,
    )
    rows = products.search("epon")
    assert len(rows) == 1
    row = rows[0]
    assert row.mention_count == 2
    assert row.supplier == "Hexion"
    assert row.generic_name == "双酚A环氧树脂"
    assert sorted(row.source_ids) == ["s1", "s2"]


def test_upsert_never_overwrites_existing(stores):
    _, _, products = stores
    products.upsert_mentions("s1", [{"trade_name": "BYK", "grade": "333", "supplier": "BYK"}], link_structures=False)
    products.upsert_mentions("s2", [{"trade_name": "BYK", "grade": "333", "supplier": "别人家"}], link_structures=False)
    assert products.search("byk")[0].supplier == "BYK"


def test_structure_link_uses_chemtools(stores, monkeypatch):
    _, _, products = stores
    from app.services import chemtools

    monkeypatch.setattr(chemtools, "gateway_enabled", lambda: True)
    monkeypatch.setattr(chemtools, "name_to_cas", lambda q: "25068-38-6")
    monkeypatch.setattr(chemtools, "name_to_smiles", lambda q: None)
    products.upsert_mentions("s1", [{"trade_name": "Epon", "grade": "828"}])
    assert products.search("epon")[0].cas == "25068-38-6"


def test_find_for_material_matches_generic_and_cas(stores):
    _, _, products = stores
    products.upsert_mentions(
        "s1",
        [{"trade_name": "Epon", "grade": "828", "generic_name": "双酚A环氧树脂", "cas": "25068-38-6"}],
        link_structures=False,
    )
    assert products.find_for_material("双酚A环氧树脂")
    assert products.find_for_material("", cas="25068-38-6")
    assert not products.find_for_material("聚氨酯")


# ── kb_index meta wiring ─────────────────────────────────────────────────────


def test_index_source_attaches_meta_and_registers_products(stores):
    _, chunks, products = stores
    from app.services import kb_index

    md = (
        "# 配方专利\n\n## 实施例 1\n\n"
        "环氧树脂 Epon 828 一百份，磷酸锌（CAS 7779-90-0）十五份，"
        "化学式 Zn3(PO4)2，固化后耐盐雾。" + "补充描述。" * 20
    )
    n = kb_index.index_source("src-meta", md, embed=False)
    assert n >= 1
    rows = chunks.get_by_source("src-meta")
    metas = [r.meta for r in rows if r.meta]
    assert metas, "chunk meta must carry extracted entities"
    chem_types = {e["type"] for m in metas for e in m.get("chem", [])}
    assert {"cas", "formula"} <= chem_types
    assert products.search("epon"), "products flow into the registry"


def test_chem_extract_flag_disables_meta(stores, monkeypatch):
    _, chunks, products = stores
    monkeypatch.setenv("FORMUMIND_CHEM_EXTRACT_ENABLED", "false")
    get_settings.cache_clear()
    from app.services import kb_index

    kb_index.index_source("src-off", "Epon 828 与 CAS 7779-90-0。" + "x" * 100, embed=False)
    rows = chunks.get_by_source("src-off")
    assert all(r.meta is None for r in rows)
    assert not products.search("epon")


# ── guide products registration ──────────────────────────────────────────────


def test_guide_products_flow_to_registry(stores, monkeypatch):
    _, _, products = stores
    from app.services import ingestion

    guide = SourceGuideSchema(
        summary="s", key_entities=["环氧树脂"], faqs=["q1"],
        products=[ProductMention(trade_name="Desmodur", grade="N75",
                                 supplier="Covestro", generic_name="HDI缩二脲", role="固化剂")],
    )
    monkeypatch.setattr(ingestion, "extract_source_guide", lambda text, title="": (guide, None))
    monkeypatch.setenv("FORMUMIND_SOURCE_GUIDE_ENABLED", "true")
    get_settings.cache_clear()
    # No API key in tests → guide path skipped; call the registration directly.
    ingestion._register_guide_products("src-g", guide)
    row = products.search("desmodur")[0]
    assert row.supplier == "Covestro"
    assert row.role == "固化剂"


def test_old_guides_without_products_still_validate():
    guide = SourceGuideSchema.model_validate(
        {"summary": "s", "key_entities": ["a"], "parameter_space": {}, "faqs": ["q"]}
    )
    assert guide.products == []


# ── products API ─────────────────────────────────────────────────────────────


def test_products_endpoint(stores):
    from fastapi.testclient import TestClient

    from app.main import app

    _, _, products = stores
    products.upsert_mentions("s1", [{"trade_name": "Aerosil", "grade": "200", "supplier": "Evonik"}], link_structures=False)
    client = TestClient(app)
    resp = client.get("/api/kb/products", params={"q": "aerosil"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["products"][0]["trade_name"] == "Aerosil"


# ── vision extraction ────────────────────────────────────────────────────────


def _fake_openai(monkeypatch, payload: dict):
    mod = types.ModuleType("openai")

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class OpenAI:
        last_kwargs: dict = {}

        def __init__(self, **kwargs):
            type(self).last_kwargs = kwargs
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kw: _Resp(json.dumps(payload, ensure_ascii=False))
                )
            )

    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return OpenAI


def test_vision_unavailable_without_key(monkeypatch):
    from app.services import vision_extract

    monkeypatch.delenv("FORMUMIND_DEEPSEEK_API_KEY", raising=False)
    get_settings.cache_clear()
    extraction, err = vision_extract.extract_image(b"fake", "table.png")
    assert extraction is None
    assert err


def test_vision_extracts_and_verifies_molecules(monkeypatch):
    from app.services import runtime_secrets, vision_extract

    rs = runtime_secrets.get_runtime_secrets()
    rs.set("llm_provider", "deepseek")
    rs.set("deepseek_api_key", "sk-test")
    _fake_openai(
        monkeypatch,
        {
            "kind": "structure",
            "markdown": "| 组分 | 份数 |\n|---|---|\n| 环氧丙烷 | 10 |",
            "molecules": [
                {"smiles": "C1CO1", "name": "环氧丙烷", "confidence": 0.9},
                {"smiles": "not-a-smiles((", "name": "未知物", "confidence": 0.8},
            ],
            "notes": "",
        },
    )
    try:
        extraction, err = vision_extract.extract_image(b"\x89PNG fake", "structure.png")
        assert err is None and extraction is not None
        assert extraction.kind == "structure"
        mols = {m.name: m for m in extraction.molecules}
        try:
            import rdkit  # noqa: F401

            assert mols["环氧丙烷"].verified is True
            assert mols["未知物"].verified is False
            assert mols["未知物"].confidence <= 0.3
        except ImportError:
            assert mols["环氧丙烷"].verified is False  # no RDKit → honest flag
        md = vision_extract.image_markdown(extraction, "structure.png")
        assert "环氧丙烷" in md and "|" in md
    finally:
        rs.clear()


def test_image_upload_routes_to_vision(monkeypatch, stores):
    from app.services import ingestion, vision_extract

    fake = vision_extract.VisionExtraction(
        kind="table", markdown="| a | b |\n|---|---|\n| 1 | 2 |", molecules=[]
    )
    monkeypatch.setattr(vision_extract, "extract_image", lambda c, f: (fake, None))
    outcome = ingestion.ingest_file("formula.png", b"\x89PNG fake")
    assert outcome.evidence
    assert outcome.source_id  # persisted as a SourceDocument
    src, _, _ = stores
    doc = src.get(outcome.source_id)
    assert doc.source_kind == "image"
    assert "| a | b |" in doc.full_text


def test_image_upload_placeholder_when_vision_off(monkeypatch, stores):
    from app.services import ingestion, vision_extract

    monkeypatch.setattr(
        vision_extract, "extract_image", lambda c, f: (None, "未配置视觉模型")
    )
    outcome = ingestion.ingest_file("photo.jpg", b"\xff\xd8 fake")
    assert outcome.source_id is None
    assert outcome.extraction_status == "skipped"
    assert "未配置视觉模型" in outcome.evidence[0].snippet
