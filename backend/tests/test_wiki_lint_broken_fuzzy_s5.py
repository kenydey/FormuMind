"""S5: broken-link fuzzy suggest + explicit apply rewrite / Related."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.main import app
from app.services.wiki.lint import (
    apply_broken_fix,
    lint_and_persist,
    list_flagged_pages,
    suggest_actions,
    suggest_broken_fixes,
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

    db_path = tmp_path / "wiki_s5.db"
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


def test_suggest_broken_fixes_fuzzy_match(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    _upsert(wiki, path="materials/other.md", kind="material", title="Other")
    fixes = suggest_broken_fixes("zinc phosphat", exclude_path="materials/other.md", limit=3)
    assert fixes
    assert fixes[0]["path"] == "materials/zinc-phosphate.md"
    assert "zinc" in fixes[0]["wikilink"].lower() or "Zinc" in fixes[0]["wikilink"]
    assert fixes[0]["score"] >= 0.48


def test_apply_broken_fix_rewrite(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    _upsert(
        wiki,
        path="materials/a.md",
        kind="material",
        title="A",
        body_extra="See [[zinc phosphat]] for details.",
    )
    lint_and_persist("materials/a.md")
    out = apply_broken_fix(
        path="materials/a.md",
        broken="zinc phosphat",
        replacement_path="materials/zinc-phosphate.md",
        mode="rewrite",
    )
    assert out["ok"] is True
    assert out["mode"] == "rewrite"
    md = wiki.read_markdown("materials/a.md") or ""
    assert "[[zinc phosphat]]" not in md.lower()
    assert "zinc-phosphate" in md.lower() or "Zinc Phosphate" in md
    assert "broken" not in (out.get("flags") or [])


def test_apply_broken_fix_append_related(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    _upsert(
        wiki,
        path="materials/a.md",
        kind="material",
        title="A",
        body_extra="No wikilink here.",
    )
    out = apply_broken_fix(
        path="materials/a.md",
        broken="zinc phosphat",
        replacement_path="materials/zinc-phosphate.md",
        mode="append_related",
    )
    assert out["mode"] == "append_related"
    md = wiki.read_markdown("materials/a.md") or ""
    assert "## Related" in md
    assert "fixed from [[zinc phosphat]]" in md


def test_suggest_actions_include_apply_chips(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    fixes = suggest_broken_fixes("zinc phosphat", exclude_path="materials/a.md")
    acts = suggest_actions(
        flags=["broken"],
        kind="material",
        path="materials/a.md",
        title="A",
        broken_targets=["zinc phosphat"],
        broken_fixes=fixes,
    )
    by_id = {a["id"]: a for a in acts}
    assert "fix_broken" in by_id
    apply = next(a for a in acts if a["id"].startswith("apply_broken_fix"))
    assert apply["replacement_path"] == "materials/zinc-phosphate.md"
    assert apply["broken"] == "zinc phosphat"
    assert apply["mode"] == "rewrite"


def test_list_flagged_emits_fuzzy_apply_actions(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    _upsert(
        wiki,
        path="materials/a.md",
        kind="material",
        title="A",
        body_extra="See [[zinc phosphat]]",
    )
    lint_and_persist("materials/a.md")
    rows = list_flagged_pages(limit=20)
    row = next(r for r in rows if r["path"] == "materials/a.md")
    assert "broken" in row["flags"]
    apply_ids = [a for a in row["actions"] if a["id"].startswith("apply_broken_fix")]
    assert apply_ids
    assert apply_ids[0]["replacement_path"] == "materials/zinc-phosphate.md"


def test_apply_broken_api(wiki_env):
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    _upsert(
        wiki,
        path="materials/a.md",
        kind="material",
        title="A",
        body_extra="See [[zinc phosphat]]",
    )
    client = TestClient(app)
    r = client.post(
        "/api/wiki/lint/apply-broken",
        json={
            "path": "materials/a.md",
            "broken": "zinc phosphat",
            "replacement_path": "materials/zinc-phosphate.md",
            "mode": "rewrite",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "rewrite"
    md = wiki.read_markdown("materials/a.md") or ""
    assert "[[zinc phosphat]]" not in md


def test_apply_broken_does_not_touch_claims_bounds(wiki_env):
    """Regression: apply only edits wikilinks / Related — not bounds_json tables."""
    wiki = wiki_env
    _upsert(wiki, path="materials/zinc-phosphate.md", kind="material", title="Zinc Phosphate")
    key = "a"
    md = dump_page(
        kind="material",
        title="A",
        entity_id="material:a",
        norm_key=key,
        source_ids=["s1"],
        summary="A.",
        flags=[],
        bounds=[{"name": "pH", "min": 1.0, "max": 3.0}],
    )
    md = md.rstrip() + "\n\n## Links\nSee [[zinc phosphat]]\n"
    wiki.upsert_page(
        path="materials/a.md",
        kind="material",
        title="A",
        norm_key=key,
        entity_id="material:a",
        markdown=md,
        source_ids=["s1"],
        flags=[],
    )
    before = wiki.read_markdown("materials/a.md") or ""
    assert "bounds_json" in before or "pH" in before
    apply_broken_fix(
        path="materials/a.md",
        broken="zinc phosphat",
        replacement_path="materials/zinc-phosphate.md",
        mode="rewrite",
    )
    after = wiki.read_markdown("materials/a.md") or ""
    assert "pH" in after
    assert "1.0" in after or "1" in after
