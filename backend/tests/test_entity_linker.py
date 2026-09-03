"""KG P0 — entity linker on indexed chunks."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.source_store import SourceStore
from app.services import kb_index
from app.services.kg.entity_linker import link_source

MD = """# 防腐专利

## 实施例 1

环氧树脂 E51 一百质量份，磷酸锌防锈颜料十五份 CAS 7779-90-0，盐雾试验七百二十小时。
牌号 Heliogen L 936 蓝色颜料 2 份。
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
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.entity_store as entity_store_mod
    import app.db.source_store as source_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_link.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    ent = EntityStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(entity_store_mod, "_store", ent)
    return src, chk, ent


def test_link_source_creates_cas_and_catalog_mentions(stores):
    src, _, ent = stores
    sid = src.create(
        filename="p.md",
        title="防腐专利",
        source_kind="patent",
        full_text=MD,
        content_hash="h1",
    )
    kb_index.index_source(sid, MD, embed=False)
    report = link_source(sid)
    assert report.mentions_upserted >= 2
    stats = ent.stats()
    assert stats["entities"] >= 2
    assert stats["mentions"] >= 2
    cas = ent.get_entity("chem:cas:7779-90-0")
    assert cas is not None


def test_index_source_triggers_link_on_ingest(stores):
    src, _, ent = stores
    sid = src.create(
        filename="p2.md",
        title="T",
        source_kind="local",
        full_text=MD,
        content_hash="h2",
    )
    n = kb_index.index_source(sid, MD, embed=False)
    assert n >= 1
    assert ent.stats()["mentions"] >= 1
