"""S2: deterministic Wiki catalog from wiki_pages + optional theme inject."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.catalog import (
    CATALOG_REL_PATH,
    build_catalog,
    list_catalog_entries,
    rebuild_catalog,
    render_catalog_markdown,
)
from app.services.wiki.schema import dump_page, system_path
from app.services.wiki.theme import compile_theme


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CATALOG_INJECT_THEMES", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_s2.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return wiki


def _upsert(wiki, *, path, kind, title, source_ids=None, flags=None):
    key = path.rsplit("/", 1)[-1].replace(".md", "")
    md = dump_page(
        kind=kind,
        title=title,
        entity_id=f"{kind}:{key}",
        norm_key=key,
        source_ids=source_ids or ["s1"],
        summary=f"{title}.",
        flags=flags or [],
    )
    wiki.upsert_page(
        path=path,
        kind=kind,
        title=title,
        norm_key=key,
        entity_id=f"{kind}:{key}",
        markdown=md,
        source_ids=source_ids or ["s1"],
        flags=list(flags or []),
    )


def test_catalog_entries_match_db_sorted(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/z-last.md", kind="material", title="Z Last")
    _upsert(wiki, path="materials/a-first.md", kind="material", title="A First")
    _upsert(wiki, path="systems/epoxy.md", kind="system", title="Epoxy")

    entries = list_catalog_entries(limit=50)
    paths = [e["path"] for e in entries]
    assert paths == sorted(paths, key=lambda p: (p.split("/")[0], p))
    assert set(paths) == {
        "materials/a-first.md",
        "materials/z-last.md",
        "systems/epoxy.md",
    }
    assert CATALOG_REL_PATH not in paths

    out = build_catalog(limit=50)
    assert out["ok"] is True
    assert out["entry_count"] == 3
    assert out["entries"] == entries
    assert "llm_overwrite: forbidden" in out["markdown"]
    assert "[[material:a-first|A First]]" in out["markdown"]


def test_rebuild_persists_catalog_md_and_matches_db(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A")
    _upsert(wiki, path="concepts/b.md", kind="concept", title="B")

    out = rebuild_catalog(persist=True, limit=50)
    assert out["persisted"] is True
    disk = wiki.root() / CATALOG_REL_PATH
    assert disk.is_file()
    text = disk.read_text(encoding="utf-8")
    assert text == out["markdown"]

    # Rebuild equality: same DB → same body lines (ignore generated_at timestamp)
    out2 = rebuild_catalog(persist=True, limit=50)
    entries1 = list_catalog_entries(limit=50)
    entries2 = list_catalog_entries(limit=50)
    assert entries1 == entries2
    md_stable = render_catalog_markdown(entries1, generated_at="FIXED")
    assert md_stable == render_catalog_markdown(entries2, generated_at="FIXED")
    assert "concepts/b.md" in out2["markdown"]


def test_catalog_api_json_and_md(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/x.md", kind="material", title="X")
    client = TestClient(app)
    r = client.get("/api/wiki/catalog", params={"limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["entry_count"] >= 1

    r_md = client.get("/api/wiki/catalog", params={"format": "md", "limit": 50})
    assert r_md.status_code == 200
    assert "text/markdown" in r_md.headers.get("content-type", "")
    assert "# Wiki Catalog" in r_md.text

    r_rebuild = client.post("/api/wiki/catalog/rebuild", json={"persist": True, "limit": 50})
    assert r_rebuild.status_code == 200
    assert r_rebuild.json()["persisted"] is True
    assert (wiki.root() / "catalog.md").is_file()


def test_theme_catalog_inject_flag_gated(wiki_env, monkeypatch):
    wiki = wiki_env
    key = "epoxy-cure"
    path = system_path(key)
    wiki.upsert_page(
        path=path,
        kind="system",
        title="环氧固化",
        norm_key=key,
        entity_id=f"system:{key}",
        markdown=dump_page(
            kind="system",
            title="环氧固化",
            entity_id=f"system:{key}",
            norm_key=key,
            source_ids=["s1"],
            summary="胺固化。",
        ),
        source_ids=["s1"],
    )
    _upsert(wiki, path="materials/resin.md", kind="material", title="Resin")

    monkeypatch.setenv("FORMUMIND_WIKI_CATALOG_INJECT_THEMES", "false")
    get_settings.cache_clear()
    out_off = compile_theme(system_key=key, use_llm=False)
    md_off = wiki.read_markdown(out_off["path"]) or ""
    assert "### Wiki catalog (deterministic)" not in md_off

    monkeypatch.setenv("FORMUMIND_WIKI_CATALOG_INJECT_THEMES", "true")
    get_settings.cache_clear()
    out_on = compile_theme(system_key=key, use_llm=False)
    md_on = wiki.read_markdown(out_on["path"]) or ""
    assert "### Wiki catalog (deterministic)" in md_on
    assert "catalog.md" in md_on or "[[material:" in md_on
