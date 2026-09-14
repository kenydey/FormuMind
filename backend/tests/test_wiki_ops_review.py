"""Q4 reserved review contract + ops endpoints for Wiki Compiled Memory."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.review import apply_page_review
from app.services.wiki.schema import dump_page, system_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_FTS_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_EMBED_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_LLM_THEMES_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_ops.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return wiki


def _seed(wiki: WikiStore, key: str = "epoxy-cure") -> str:
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
            source_ids=["s1"],
            flags=["unreviewed"],
            summary="注意催化剂窗口。",
            evidence_blocks=["### Source `s1`", "催化剂 ≤1.5 wt%。"],
        ),
        source_ids=["s1"],
        flags=["unreviewed"],
        replace_source_ids=True,
    )
    return path


def test_apply_page_review_marks_reviewed(wiki_env):
    wiki = wiki_env
    path = _seed(wiki)
    out = apply_page_review(path, reviewed=True, human_override="确认窗口合理")
    assert out["ok"] is True
    md = wiki.read_markdown(path) or ""
    assert "reviewed: true" in md
    assert "human_override:" in md
    assert "确认窗口合理" in md
    row = wiki.get_by_path(path)
    assert row is not None
    assert "unreviewed" not in (row.flags or [])


def test_review_api(wiki_env):
    path = _seed(wiki_env)
    with TestClient(app) as c:
        r = c.post(
            "/api/wiki/pages/review",
            json={"path": path, "reviewed": True, "human_override": "ops note"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert "unreviewed" not in (body.get("flags") or [])


def test_embed_rebuild_409_when_off(wiki_env):
    _seed(wiki_env)
    with TestClient(app) as c:
        r = c.post("/api/wiki/embed/rebuild")
        assert r.status_code == 409


def test_fts_rebuild_ok(wiki_env):
    _seed(wiki_env)
    with TestClient(app) as c:
        r = c.post("/api/wiki/fts/rebuild")
        assert r.status_code == 200
        assert r.json().get("ok") is True
