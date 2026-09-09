"""W3–W4 LLM Wiki: retrieve/chat blend, DOE bounds, lint, systems compile."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import (
    Evidence,
    ParameterBoundary,
    ProductMention,
    Requirement,
    SourceGuideSchema,
)
from app.main import app
from app.services.factor_suggest import suggest_factors
from app.services.llm import _build_context
from app.services.wiki import compile as wiki_compile
from app.services.wiki.constraints import wiki_parameter_bounds
from app.services.wiki.lint import lint_page, list_flagged_pages
from app.services.wiki.retrieve import blend_wiki_evidence, filter_raw_evidence, search_wiki
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_LINT_ON_COMPILE", "true")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki34.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return src, chk, wiki, wiki_root


def _guide():
    return SourceGuideSchema(
        summary="环氧固化机理：胺与环氧基开环交联。",
        key_entities=["E-51", "催化剂"],
        faqs=["如何避免催化剂过量导致暴聚？", "固化温度？"],
        products=[ProductMention(trade_name="E-51", role="树脂")],
        parameter_space={
            "catalyst_wt": ParameterBoundary(min_value=0.1, max_value=1.5, unit="wt%"),
        },
        status="verified",
    )


def test_wiki_retrieve_hits_title(wiki_env):
    _, _, wiki, _ = wiki_env
    wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown=dump_page(
            kind="material",
            title="E-51",
            entity_id="material:e51",
            norm_key="e51",
            source_ids=["s1"],
            summary="环氧树脂牌号 E-51 用于防腐底漆。",
        ),
        source_ids=["s1"],
    )
    hits = search_wiki("E-51 环氧", k=5)
    assert hits
    assert hits[0].identifier.startswith("wiki:")
    assert hits[0].source == "wiki"


def test_blend_and_claims_filter(wiki_env):
    _, _, wiki, _ = wiki_env
    wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown=dump_page(
            kind="material",
            title="E-51",
            entity_id="material:e51",
            norm_key="e51",
            source_ids=["s1"],
            summary="E-51 环氧树脂",
        ),
        source_ids=["s1"],
    )
    raw = [
        Evidence(
            source="literature",
            identifier="doi:1",
            title="paper",
            snippet="raw chunk about epoxy",
            relevance=0.9,
        )
    ]
    merged, n = blend_wiki_evidence("E-51", raw)
    assert n >= 1
    assert any(e.source == "wiki" for e in merged)
    claims_pool = filter_raw_evidence(merged)
    assert all(e.source != "wiki" for e in claims_pool)
    ctx = _build_context(merged)
    assert "Wiki编译结论" in ctx or "Compiled Wiki" in ctx
    assert "原始摘录" in ctx or "Raw evidence" in ctx


def test_chat_wiki_blend_off(wiki_env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "false")
    get_settings.cache_clear()
    _, _, wiki, _ = wiki_env
    wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown=dump_page(
            kind="material",
            title="E-51",
            entity_id="material:e51",
            norm_key="e51",
            source_ids=["s1"],
            summary="E-51",
        ),
        source_ids=["s1"],
    )
    merged, n = blend_wiki_evidence("E-51", [])
    assert n == 0
    assert merged == []


def test_compile_systems_bounds_and_pitfalls(wiki_env):
    src, _, wiki, _ = wiki_env
    sid = src.create(
        filename="p.pdf",
        title="guide",
        source_kind="patent",
        full_text="# x\n" + ("环氧 " * 40),
        content_hash="c1",
        source_guide=_guide(),
    )
    out = wiki_compile.compile_source(sid)
    assert out["ok"]
    assert wiki.get_by_path("systems/catalystwt.md") or wiki.get_by_path(
        "systems/catalyst_wt.md"
    ) or any("systems/" in p for p in out["pages"])
    bounds = wiki_parameter_bounds()
    assert any(b["name"] == "catalyst_wt" for b in bounds)
    assert any(p.startswith("pitfalls/") for p in out["pages"])
    assert any(p.startswith("mechanisms/") for p in out["pages"])


def test_factor_suggest_wiki_bounds(wiki_env, monkeypatch):
    _, _, wiki, _ = wiki_env
    wiki.upsert_page(
        path="systems/catalystwt.md",
        kind="system",
        title="catalyst_wt",
        norm_key="catalystwt",
        entity_id="system:catalystwt",
        markdown=dump_page(
            kind="system",
            title="catalyst_wt",
            entity_id="system:catalystwt",
            norm_key="catalystwt",
            source_ids=["s1"],
            summary="catalyst window",
            bounds=[{"name": "catalyst_wt", "min": 0.2, "max": 1.0, "unit": "wt%"}],
        ),
        source_ids=["s1"],
    )
    # Patch resolve path: use a requirement that yields a matching factor name.
    from app.domain import schemas as sch

    # Minimal requirement — suggest_factors uses levers; monkeypatch levers_to_doe_factors
    import app.services.factor_suggest as fs

    class _F:
        name = "catalyst_wt"
        low = 0.0
        high = 5.0
        unit = "wt%"

    monkeypatch.setattr(fs, "resolve_levers", lambda *a, **k: [])
    monkeypatch.setattr(fs, "levers_to_doe_factors", lambda *a, **k: [_F()])
    monkeypatch.setattr(fs.kb_index, "aggregate_parameter_space", lambda: {})
    monkeypatch.setattr(fs.kb_index, "doe_parameter_hints", lambda names: [])

    req = Requirement.model_validate(
        {
            "product_name": "test",
            "domain": "anticorrosion_coating",
            "objectives": [{"metric": "adhesion", "direction": "maximize", "weight": 1.0}],
        }
    )
    cands = suggest_factors(req)
    assert len(cands) == 1
    assert cands[0].high <= 1.0 + 1e-9
    assert cands[0].low >= 0.2 - 1e-9
    assert "wiki" in (cands[0].source or "")


def test_wiki_lint_and_flags_api(wiki_env):
    _, _, wiki, _ = wiki_env
    wiki.upsert_page(
        path="materials/orphan.md",
        kind="material",
        title="orphan",
        norm_key="orphan",
        entity_id="material:orphan",
        markdown=dump_page(
            kind="material",
            title="orphan",
            entity_id="material:orphan",
            norm_key="orphan",
            source_ids=[],
            summary="no evidence",
        ),
        source_ids=[],
    )
    flags = lint_page("materials/orphan.md")
    assert "stale" in flags
    client = TestClient(app)
    r = client.get("/api/wiki/flags")
    assert r.status_code == 200
