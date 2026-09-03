"""Chemistry-aware Q&A / retrieval / recommend grounding (KB stream P3)."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory


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
    return source_store_mod._store, chunk_store_mod._store, product_store_mod._store


def _seed_chunks(chunk_store):
    """Two chunks with distinct entities; scores rely on the keyword path."""
    chunk_store.replace_for_source(
        "src-a",
        [
            {
                "text": "本实施例采用磷酸锌缓蚀颜料改进耐蚀性能，用量十五份。",
                "heading_path": "实施例 1",
                "meta": {"chem": [{"type": "cas", "value": "7779-90-0"},
                                  {"type": "formula", "value": "Zn3(PO4)2"}]},
            },
        ],
    )
    chunk_store.replace_for_source(
        "src-b",
        [
            {
                "text": "对比样使用滑石粉填料，耐蚀性能较差，用量十五份。",
                "heading_path": "对比例",
                "meta": {"chem": [{"type": "formula", "value": "Mg3Si4O10(OH)2"}]},
            },
        ],
    )


# ── entity-boosted retrieval ─────────────────────────────────────────────────


def test_cas_in_question_boosts_matching_chunk(stores):
    _, chunks, _ = stores
    _seed_chunks(chunks)
    from app.services import kb_index

    hits = kb_index.search_chunks("CAS 7779-90-0 的用量是多少 份", k=2)
    assert hits, "entity match must retrieve even with weak keyword overlap"
    assert "磷酸锌" in hits[0].snippet


def test_formula_in_question_boosts_matching_chunk(stores):
    _, chunks, _ = stores
    _seed_chunks(chunks)
    from app.services import kb_index

    hits = kb_index.search_chunks("Zn3(PO4)2 用量 十五份", k=2)
    assert hits and "磷酸锌" in hits[0].snippet


def test_trade_name_expands_via_product_registry(stores):
    _, chunks, products = stores
    products.upsert_mentions(
        "src-a",
        [{"trade_name": "ZP", "grade": "10", "generic_name": "磷酸锌", "cas": "7779-90-0"}],
        link_structures=False,
    )
    chunks.replace_for_source(
        "src-a",
        [{"text": "磷酸锌缓蚀颜料在环氧底漆中的作用机制与用量。", "heading_path": ""}],
    )
    from app.services import kb_index

    # Query mentions only the trade name (needs supplier context to be
    # recognised) — registry expansion must bring back the generic-name chunk.
    hits = kb_index.search_chunks("购自某厂商的 ZP 10 在底漆中的作用", k=3)
    assert hits and "磷酸锌" in hits[0].snippet


def test_no_chem_entities_keeps_scoring_unchanged(stores):
    _, chunks, _ = stores
    _seed_chunks(chunks)
    from app.services import kb_index

    hits = kb_index.search_chunks("滑石粉 填料 对比样", k=1)
    assert hits and "滑石粉" in hits[0].snippet


# ── product hints in recommend prompt ────────────────────────────────────────


def test_product_hints_lines(stores):
    _, _, products = stores
    products.upsert_mentions(
        "s1",
        [{"trade_name": "Epon", "grade": "828", "supplier": "Hexion",
          "generic_name": "双酚A环氧树脂", "role": "树脂"}],
        link_structures=False,
    )
    from app.domain.schemas import MaterialSpec
    from app.services import kb_index

    lines = kb_index.product_hints([MaterialSpec(name="双酚A环氧树脂")])
    assert len(lines) == 1
    assert "Epon 828" in lines[0]
    assert "Hexion" in lines[0]
    assert "语料提及" in lines[0]


def test_product_hints_empty_offline(stores):
    from app.domain.schemas import MaterialSpec
    from app.services import kb_index

    assert kb_index.product_hints([MaterialSpec(name="不存在的材料")]) == []


def test_recommend_prompt_includes_product_block(stores, monkeypatch):
    _, _, products = stores
    products.upsert_mentions(
        "s1",
        [{"trade_name": "Aerosil", "grade": "200", "supplier": "Evonik",
          "generic_name": "气相二氧化硅", "role": "触变剂"}],
        link_structures=False,
    )
    from app.domain.objective_contract import normalize_objectives
    from app.domain.schemas import MaterialSpec, ProductDomain, Requirement
    from app.services.llm import _recommend_user_prompt

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        materials=[MaterialSpec(name="气相二氧化硅")],
    )
    prompt = _recommend_user_prompt(req, normalize_objectives(req), [], 3)
    assert "常用商业牌号" in prompt
    assert "Aerosil 200" in prompt


def test_recommend_prompt_no_block_without_products(stores):
    from app.domain.objective_contract import normalize_objectives
    from app.domain.schemas import ProductDomain, Requirement
    from app.services.llm import _recommend_user_prompt

    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    prompt = _recommend_user_prompt(req, normalize_objectives(req), [], 3)
    assert "常用商业牌号" not in prompt


# ── chat prompt notation rules ───────────────────────────────────────────────


def test_chat_prompt_contains_chemistry_notation_rules():
    from app.domain.schemas import Evidence
    from app.services.llm import _chat_prompt

    prompt = _chat_prompt(
        "环氧固化反应式？",
        [Evidence(source="kb", identifier="x", title="t", snippet="s", relevance=0.9)],
        "anticorrosion_coating",
    )
    assert "$$" in prompt
    assert "smiles" in prompt.lower()
    assert "trade names" in prompt


# ── stats products counter ───────────────────────────────────────────────────


def test_kb_stats_reports_products(stores):
    _, _, products = stores
    products.upsert_mentions("s1", [{"trade_name": "BYK", "grade": "333"}], link_structures=False)
    from app.services import kb_index

    stats = kb_index.kb_stats()
    assert stats["products"] == 1
