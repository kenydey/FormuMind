"""Phase 2: Wiki FTS + L2 theme compile (flag-gated)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.fts import rebuild_all, search_fts
from app.services.wiki.schema import dump_page, system_path, theme_path
from app.services.wiki.theme import compile_theme


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_FTS_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_p2.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return wiki, factory


def _seed_system(wiki: WikiStore, key: str = "epoxy-cure") -> None:
    path = system_path(key)
    wiki.upsert_page(
        path=path,
        kind="system",
        title="环氧固化体系",
        norm_key=key,
        entity_id=f"system:{key}",
        markdown=dump_page(
            kind="system",
            title="环氧固化体系",
            entity_id=f"system:{key}",
            norm_key=key,
            source_ids=["s1", "s2"],
            summary="胺固化环氧体系，注意催化剂窗口与暴聚风险。",
            evidence_blocks=["### Source `s1`", "催化剂建议 ≤1.5 wt%。"],
            bounds=[{"name": "catalyst_wt", "min": 0.1, "max": 1.5, "unit": "wt%"}],
        ),
        source_ids=["s1", "s2"],
        replace_source_ids=True,
    )


def test_fts_indexes_on_upsert_and_finds_body(wiki_env):
    wiki, factory = wiki_env
    _seed_system(wiki)
    rebuild_all(factory, wiki)
    hits = search_fts(factory, "暴聚", limit=10)
    assert hits
    assert any("epoxy" in h["path"] or "环氧" in (h["title"] or "") for h in hits)
    assert any("暴聚" in (h.get("snippet") or "") for h in hits)


def test_fts_match_query_expands_cjk():
    from app.services.wiki.fts import _match_query

    assert _match_query("暴聚") == '("暴" AND "聚")'
    assert "epoxy" in (_match_query("epoxy 固化") or "")


def test_search_api_fts(wiki_env):
    wiki, _ = wiki_env
    _seed_system(wiki)
    with TestClient(app) as c:
        c.post("/api/wiki/fts/rebuild")
        r = c.get("/api/wiki/search", params={"q": "暴聚"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        assert body["mode"] == "fts"
        assert any(h.get("kind") == "system" for h in body["hits"])


def test_theme_compile_requires_flag(wiki_env, monkeypatch):
    wiki, _ = wiki_env
    _seed_system(wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(PermissionError):
        compile_theme(system_key="epoxy-cure", use_llm=False)


def test_theme_compile_when_enabled(wiki_env, monkeypatch):
    wiki, _ = wiki_env
    _seed_system(wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "true")
    get_settings.cache_clear()
    out = compile_theme(system_key="epoxy-cure", use_llm=False)
    assert out["ok"] is True
    assert out["path"] == theme_path("epoxy-cure")
    md = wiki.read_markdown(out["path"]) or ""
    assert "体系综述" in md or "system_overview" in md or "L1" in md
    assert "source_ids" in md
    row = wiki.get_by_path(out["path"])
    assert row is not None
    assert row.kind == "theme"
    assert "unreviewed" in (row.flags or [])


def test_theme_compile_api_409_when_off(wiki_env):
    wiki, _ = wiki_env
    _seed_system(wiki)
    with TestClient(app) as c:
        r = c.post(
            "/api/wiki/themes/compile",
            json={"system_key": "epoxy-cure", "use_llm": False},
        )
        assert r.status_code == 409


def test_theme_compile_api_when_on(wiki_env, monkeypatch):
    wiki, _ = wiki_env
    _seed_system(wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "true")
    get_settings.cache_clear()
    with TestClient(app) as c:
        r = c.post(
            "/api/wiki/themes/compile",
            json={"system_key": "epoxy-cure", "use_llm": False},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("path", "").startswith("themes/")


def test_theme_excluded_from_doe_bounds(wiki_env, monkeypatch):
    """L2 theme pages must not contribute DOE constraint extraction."""
    wiki, _ = wiki_env
    _seed_system(wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    get_settings.cache_clear()
    out = compile_theme(system_key="epoxy-cure", use_llm=False)
    # Poison theme front-matter with a fake bound (should still be ignored)
    poisoned = (wiki.read_markdown(out["path"]) or "").replace(
        "reviewed: false",
        'reviewed: false\nbounds_json: [{"name": "poison_wt", "min": 0, "max": 1}]',
        1,
    )
    wiki.upsert_page(
        path=out["path"],
        kind="theme",
        title="poisoned theme",
        norm_key="epoxy-cure",
        entity_id="theme:system:epoxy-cure",
        markdown=poisoned,
        source_ids=["s1"],
        flags=["unreviewed"],
        replace_source_ids=True,
    )
    from app.services.wiki.constraints import wiki_parameter_bounds

    names = {b["name"] for b in wiki_parameter_bounds()}
    assert "poison_wt" not in names
    assert "catalyst_wt" in names  # L1 system still contributes
