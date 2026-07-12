"""KB P3 tests — recommendation grounding via persistent KB + DOE parameter fusion."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import Evidence, Requirement
from app.services import kb_index


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    return src, chk


# ── retrieve_node KB fusion ──────────────────────────────────────────────────


def _kb_hit(ident: str = "kb:s1#c0") -> Evidence:
    return Evidence(
        source="patent", identifier=ident, title="专利 · 实施例 1",
        snippet="环氧底漆磷酸锌十五份，盐雾七百二十小时。", relevance=0.85,
    )


def test_retrieve_node_merges_kb_chunks(monkeypatch, stores):
    from app.pipeline.research_graph import retrieve_node

    monkeypatch.setattr(
        "app.services.kb_index.search_chunks", lambda q, k=4: [_kb_hit()]
    )
    state = retrieve_node({"topic": "环氧防腐底漆", "query": "环氧防腐底漆"})
    ids = [e.identifier for e in state["evidence"]]
    assert "kb:s1#c0" in ids


def test_retrieve_node_dedupes_kb_against_colbert(monkeypatch, stores):
    from app.pipeline.research_graph import retrieve_node

    hit = _kb_hit()
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda q, k=4: [hit])
    state = retrieve_node(
        {"topic": "环氧防腐底漆", "query": "环氧防腐底漆", "pre_index": [hit]}
    )
    ids = [e.identifier for e in state["evidence"]]
    assert ids.count("kb:s1#c0") == 1


def test_retrieve_node_kb_disabled_no_call(monkeypatch, stores):
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    from app.pipeline.research_graph import retrieve_node

    called = []
    monkeypatch.setattr(
        "app.services.kb_index.search_chunks", lambda q, k=4: called.append(q) or []
    )
    retrieve_node({"topic": "环氧防腐底漆", "query": "环氧防腐底漆"})
    assert called == []


def test_retrieve_node_kb_topk_zero_no_call(monkeypatch, stores):
    monkeypatch.setenv("FORMUMIND_KB_RECOMMEND_TOP_K", "0")
    get_settings.cache_clear()
    from app.pipeline.research_graph import retrieve_node

    called = []
    monkeypatch.setattr(
        "app.services.kb_index.search_chunks", lambda q, k=4: called.append(q) or []
    )
    retrieve_node({"topic": "环氧防腐底漆", "query": "环氧防腐底漆"})
    assert called == []


# ── parameter-space aggregation ──────────────────────────────────────────────


def _guide(space: dict) -> dict:
    return {
        "summary": "s", "key_entities": ["e"], "parameter_space": space,
        "faqs": ["q"], "status": "verified",
    }


def test_aggregate_parameter_space_unions_ranges(stores):
    src, _ = stores
    with src._session_factory() as session:
        from app.db.models import SourceDocument

        session.add(SourceDocument(
            id="a", filename="a", title="a", source_kind="local", content_hash="ha",
            full_text="x", raw_text_chars=1,
            source_guide=_guide({"固化温度": {"min_value": 60, "max_value": 80, "unit": "°C"}}),
        ))
        session.add(SourceDocument(
            id="b", filename="b", title="b", source_kind="patent", content_hash="hb",
            full_text="x", raw_text_chars=1,
            source_guide=_guide({"固化温度": {"min_value": 70, "max_value": 120, "unit": "°C"},
                                 "磷酸锌": {"min_value": 5, "max_value": 20, "unit": "wt%"}}),
        ))
        session.commit()

    fused = kb_index.aggregate_parameter_space()
    assert fused["固化温度"]["min"] == 60
    assert fused["固化温度"]["max"] == 120
    assert fused["固化温度"]["sources"] == 2
    assert fused["磷酸锌"]["unit"] == "wt%"


def test_aggregate_parameter_space_empty_and_disabled(monkeypatch, stores):
    assert kb_index.aggregate_parameter_space() == {}
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    assert kb_index.aggregate_parameter_space() == {}


def test_doe_parameter_hints_match_factor_names(monkeypatch, stores):
    monkeypatch.setattr(
        kb_index, "aggregate_parameter_space",
        lambda: {"固化温度": {"min": 60, "max": 120, "unit": "°C", "sources": 2}},
    )
    hints = kb_index.doe_parameter_hints(["固化温度", "磷酸锌用量"])
    assert len(hints) == 1
    assert "固化温度" in hints[0]
    assert "60–120 °C" in hints[0]
    assert "2 个来源" in hints[0]


def test_doe_parameter_hints_no_match_is_silent(monkeypatch, stores):
    monkeypatch.setattr(
        kb_index, "aggregate_parameter_space",
        lambda: {"烘烤时间": {"min": 10, "max": 30, "unit": "min", "sources": 1}},
    )
    assert kb_index.doe_parameter_hints(["固化温度"]) == []


def test_build_doe_appends_kb_hints(monkeypatch, stores):
    from app.pipeline.workflow import build_doe

    monkeypatch.setattr(
        "app.services.kb_index.doe_parameter_hints",
        lambda names: [f"知识库文献范围：{names[0]} ≈ 1–2 %（1 个来源）"] if names else [],
    )
    req = Requirement(domain="anticorrosion_coating", description="环氧防腐底漆")
    plan = build_doe(req)
    assert "知识库文献范围" in plan.notes


def test_build_doe_unchanged_without_kb_hints(monkeypatch, stores):
    from app.pipeline.workflow import build_doe

    monkeypatch.setattr("app.services.kb_index.doe_parameter_hints", lambda names: [])
    req = Requirement(domain="anticorrosion_coating", description="环氧防腐底漆")
    plan = build_doe(req)
    assert "知识库文献范围" not in (plan.notes or "")
