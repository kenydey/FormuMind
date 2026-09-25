"""Top-5′ #4: async relations rebuild works without kg_relation_extract_enabled."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.source_store import SourceStore
from app.services.kg.entity_linker import link_source, rebuild_relations


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_mod
    import app.db.entity_store as ent_mod
    import app.db.source_store as src_mod

    engine = make_engine(f"sqlite:///{tmp_path}/rel.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    ent = EntityStore(factory)
    monkeypatch.setattr(src_mod, "_store", src)
    monkeypatch.setattr(chunk_mod, "_store", chk)
    monkeypatch.setattr(ent_mod, "_store", ent)
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KG_ENTITIES_ON_INGEST", "true")
    monkeypatch.setenv("FORMUMIND_KG_RELATIONS_ON_INGEST", "false")
    monkeypatch.setenv("FORMUMIND_KG_RELATION_EXTRACT_ENABLED", "false")
    get_settings.cache_clear()
    return src, chk, ent


def test_rebuild_relations_force_bypasses_extract_flag(stores):
    src, chk, _ent = stores
    sid = src.create(
        filename="rel.pdf",
        title="Epoxy replaces alkyd primer",
        source_kind="paper",
        full_text="Zinc phosphate substitutes for chromate in epoxy primer. "
        "Epoxy synergizes with polyamide hardener under salt spray.",
        content_hash="h-rel-1",
        project_id="p-rel",
    )
    chk.replace_for_source(
        sid,
        [
            {
                "text": (
                    "Zinc phosphate substitutes for chromate pigment in epoxy. "
                    "Epoxy resin synergizes with polyamide curing agent."
                ),
                "meta": {
                    "chem": [
                        {"type": "cas", "value": "7779-90-0"},
                        {"type": "cas", "value": "1333-82-0"},
                    ]
                },
            }
        ],
    )
    # Mentions without relations (ingest relations off).
    link_source(sid)
    off = rebuild_relations([sid], force=False)
    assert off.get("skipped") == "relation_extract_disabled" or off["relations_upserted"] == 0

    on = rebuild_relations([sid], force=True, limit=10)
    assert on["rebuilt_sources"] == 1
    assert "relations_upserted" in on
