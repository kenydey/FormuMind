"""S1: Wiki lint action loop — broken flags, link candidates, sweep."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.lint import (
    lint_and_persist,
    lint_page,
    list_flagged_pages,
    suggest_actions,
    suggest_link_candidates,
    sweep_flagged_pages,
)
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
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
    return wiki


def _upsert(wiki, *, path, kind, title, body_extra="", source_ids=None, flags=None):
    key = path.rsplit("/", 1)[-1].replace(".md", "")
    sids = source_ids if source_ids is not None else ["s1"]
    md = dump_page(
        kind=kind,
        title=title,
        entity_id=f"{kind}:{key}",
        norm_key=key,
        source_ids=sids,
        summary=f"{title}.",
        flags=flags or [],
    )
    if body_extra:
        md = md.rstrip() + "\n\n## Links\n" + body_extra + "\n"
    wiki.upsert_page(
        path=path,
        kind=kind,
        title=title,
        norm_key=key,
        entity_id=f"{kind}:{key}",
        markdown=md,
        source_ids=sids,
        flags=list(flags or []),
    )


def test_lint_detects_broken_wikilink(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="See [[ghost-missing]]")
    flags = lint_page("materials/a.md")
    assert "broken" in flags
    lint_and_persist("materials/a.md")
    row = wiki.get_by_path("materials/a.md")
    assert "broken" in (row.flags or [])


def test_orphan_actions_include_link_candidate_target(wiki_env):
    wiki = wiki_env
    _upsert(
        wiki,
        path="themes/project-demo.md",
        kind="theme",
        title="Dossier",
        source_ids=["s1"],
    )
    _upsert(wiki, path="materials/lonely.md", kind="material", title="Lonely", source_ids=["s1"])
    cands = suggest_link_candidates(path="materials/lonely.md", source_ids=["s1"])
    assert cands
    assert cands[0]["path"].startswith("themes/")
    acts = suggest_actions(
        flags=["orphan"],
        kind="material",
        path="materials/lonely.md",
        title="Lonely",
        source_ids=["s1"],
        link_candidates=cands,
    )
    by_id = {a["id"]: a for a in acts}
    assert "link_from_theme" in by_id
    assert by_id["link_from_theme"].get("target") == cands[0]["path"]


def test_list_flagged_includes_norm_key_and_targets(wiki_env):
    wiki = wiki_env
    _upsert(
        wiki,
        path="themes/project-demo.md",
        kind="theme",
        title="Dossier",
        source_ids=["s1"],
    )
    _upsert(
        wiki,
        path="materials/lonely.md",
        kind="material",
        title="Lonely",
        source_ids=["s1"],
        flags=["orphan", "unreviewed"],
    )
    rows = list_flagged_pages(limit=20)
    row = next(r for r in rows if r["path"] == "materials/lonely.md")
    assert row.get("norm_key") == "lonely"
    ids = {a["id"] for a in row["actions"]}
    assert "mark_reviewed" in ids
    assert "link_from_theme" in ids
    link = next(a for a in row["actions"] if a["id"] == "link_from_theme")
    assert link.get("target", "").startswith("themes/")


def test_sweep_clears_obsolete_orphan(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="[[Lonely]]")
    _upsert(
        wiki,
        path="materials/lonely.md",
        kind="material",
        title="Lonely",
        flags=["orphan"],
    )
    # After hub links to Lonely, sweep should drop orphan
    out = sweep_flagged_pages(limit=50, detect_orphan=True)
    assert out["ok"] is True
    row = wiki.get_by_path("materials/lonely.md")
    assert "orphan" not in (row.flags or [])


def test_lint_sweep_api(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A", body_extra="[[ghost]]", flags=["stale"])
    client = TestClient(app)
    r = client.post("/api/wiki/lint/sweep", json={"limit": 50, "detect_orphan": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["scanned"] >= 1
