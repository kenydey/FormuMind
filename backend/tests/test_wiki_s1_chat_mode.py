"""S1: wiki_chat_mode blend ordering; Claims stay Raw-only; settings API."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.domain.schemas import Evidence
from app.services.wiki.retrieve import blend_wiki_evidence, filter_raw_evidence
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_MODE", "balanced")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_s1.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()

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
    return wiki


def test_blend_wiki_first_orders_wiki_ahead(wiki_env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_MODE", "wiki_first")
    get_settings.cache_clear()
    raw = [
        Evidence(
            source="literature",
            identifier="doi:1",
            title="paper",
            snippet="raw epoxy",
            relevance=0.5,
        )
    ]
    merged, n = blend_wiki_evidence("E-51 环氧", raw)
    assert n >= 1
    assert merged[0].source == "wiki"
    assert merged[0].identifier.startswith("wiki:")
    claims = filter_raw_evidence(merged)
    assert all(c.source != "wiki" for c in claims)
    assert any(c.identifier == "doi:1" for c in claims)


def test_blend_raw_first_orders_wiki_after(wiki_env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_MODE", "raw_first")
    get_settings.cache_clear()
    raw = [
        Evidence(
            source="literature",
            identifier="doi:1",
            title="paper",
            snippet="raw epoxy",
            relevance=0.5,
        )
    ]
    merged, n = blend_wiki_evidence("E-51 环氧", raw)
    assert n >= 1
    assert merged[0].identifier == "doi:1"
    assert any(e.source == "wiki" for e in merged)


def test_wiki_chat_mode_settings_roundtrip(monkeypatch):
    pytest.importorskip("loguru")
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/api/settings/wiki-chat-mode")
        assert r.status_code == 200
        body = r.json()
        assert body["current"] in ("balanced", "wiki_first", "raw_first")
        assert len(body["choices"]) == 3
        r2 = c.post("/api/settings/wiki-chat-mode", json={"mode": "wiki_first"})
        assert r2.status_code == 200
        assert r2.json()["mode"] == "wiki_first"
        get_settings.cache_clear()
        r3 = c.get("/api/settings/wiki-chat-mode")
        assert r3.json()["current"] == "wiki_first"
        c.post("/api/settings/wiki-chat-mode", json={"mode": "balanced"})
