"""KG P0 — retrieval fusion kernel."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.source_store import SourceStore
from app.domain.schemas import Evidence
from app.services import kb_index
from app.services.kg.entity_linker import link_source
from app.services.kg.entity_resolver import resolve_query
from app.services.kg.retrieval import retrieve

MD = """# 含锌专利

## 实施例

磷酸锌 fifteen parts，盐雾 720 h。CAS 7779-90-0。
"""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.entity_store as entity_store_mod
    import app.db.source_store as source_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_ret.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    ent = EntityStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(entity_store_mod, "_store", ent)

    sid = src.create(
        filename="zinc.md",
        title="含锌专利",
        source_kind="patent",
        full_text=MD,
        content_hash="hz",
    )
    kb_index.index_source(sid, MD, embed=False)
    link_source(sid)
    return sid, ent


def test_resolve_query_finds_cas(corpus):
    resolved = resolve_query("7779-90-0 有哪些实施例")
    assert resolved.mode == "enumerative"
    assert any(c.cas_no == "7779-90-0" for c in resolved.chemicals)


def test_retrieve_disabled_falls_back_to_kb(monkeypatch, corpus):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "false")
    get_settings.cache_clear()
    result = retrieve("磷酸锌 盐雾", k_semantic=3)
    assert result.plan.mode == "semantic"
    assert result.evidence


def test_retrieve_attaches_entity_refs(corpus):
    result = retrieve("列举含 7779-90-0 的实施例", mode="enumerative")
    assert result.evidence
    assert any(ev.entity_refs for ev in result.evidence)


def test_retrieve_respects_llm_cap(corpus, monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_ENUMERATIVE_LLM_CAP", "2")
    get_settings.cache_clear()
    pre = [
        Evidence(
            source="seed",
            identifier="seed:1",
            title="seed",
            snippet="seed",
            relevance=0.5,
        )
    ]
    result = retrieve("7779-90-0", mode="hybrid", pre_evidence=pre)
    assert len(result.evidence) <= 2
