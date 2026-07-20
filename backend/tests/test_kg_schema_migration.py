"""KG P0 — schema migration and entity store basics."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.models import KGEntity, KGMention


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


def test_kg_tables_created(entity_store):
    with entity_store._session_factory() as session:
        session.add(
            KGEntity(
                id="chem:cas:1314-13-2",
                kind="chemical",
                canonical_name="1314-13-2",
                cas_no="1314-13-2",
                composition_status="resolved",
            )
        )
        session.add(
            KGMention(
                id="m1",
                entity_id="chem:cas:1314-13-2",
                source_id="s1",
                chunk_id="c1",
                surface_form="1314-13-2",
            )
        )
        session.commit()
    stats = entity_store.stats()
    assert stats["entities"] == 1
    assert stats["mentions"] == 1


def test_search_entities_case_insensitive(entity_store):
    with entity_store._session_factory() as session:
        entity_store.upsert_entity(
            session,
            id="tp:heliogen_l_936",
            kind="trade_product",
            canonical_name="Heliogen L 936",
            linked_product_key="heliogen_l_936",
            composition_status="unknown",
        )
        session.commit()
    hits = entity_store.search_entities("heliogen", limit=5)
    assert hits and hits[0].canonical_name == "Heliogen L 936"
