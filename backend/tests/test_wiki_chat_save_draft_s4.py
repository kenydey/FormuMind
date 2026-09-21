"""S4: Chat/Research → Wiki query drafts (L2 unreviewed; not Claims/DOE)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.domain.schemas import Evidence
from app.main import app
from app.services.chat_claims import build_sourced_claims
from app.services.wiki.constraints import (
    _is_doe_excluded_page,
    wiki_doe_hint_lines,
    wiki_parameter_bounds,
)
from app.services.wiki.draft_save import save_chat_draft
from app.services.wiki.retrieve import filter_raw_evidence, is_wiki_evidence
from app.services.wiki.schema import dump_page, query_draft_path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_SAVE_DRAFT", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_DOE_CONSTRAINTS", "true")
    monkeypatch.setenv("FORMUMIND_CHAT_CLAIM_CHECK_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_s4.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return wiki


def test_save_chat_draft_writes_queries_prefix(wiki_env):
    wiki = wiki_env
    out = save_chat_draft(
        project_id="proj-s4",
        question="盐雾窗口如何设定？",
        answer_markdown="建议 ≥500h，以 Raw 为准。",
        origin="chat",
        source_ids=["src-1"],
    )
    assert out["ok"] is True
    assert out["path"].startswith("queries/project-proj-s4-")
    assert out["path"].endswith(".md")
    assert "unreviewed" in out["flags"] and "draft" in out["flags"]
    assert out["disclaimer"] == "draft_not_claims"

    row = wiki.get_by_path(out["path"])
    assert row is not None
    assert row.kind == "query"
    md = wiki.read_markdown(out["path"]) or ""
    assert "盐雾窗口" in md
    assert "draft_not_claims" in md or "not Claims" in md
    assert "bounds_json" not in md


def test_save_requires_flag(wiki_env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_CHAT_SAVE_DRAFT", "false")
    get_settings.cache_clear()
    with pytest.raises(PermissionError):
        save_chat_draft(
            project_id="p1",
            question="q",
            answer_markdown="a",
        )


def test_draft_api_and_project_list(wiki_env):
    client = TestClient(app)
    r = client.post(
        "/api/wiki/drafts/save",
        json={
            "project_id": "proj-api",
            "question": "VOC 上限？",
            "answer_markdown": "文献提示 ≤420 g/L。",
            "origin": "deep_research",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["path"].startswith("queries/project-proj-api-")

    listed = client.get("/api/wiki/pages", params={"project_id": "proj-api", "limit": 50})
    assert listed.status_code == 200
    paths = {p["path"] for p in listed.json()["pages"]}
    assert body["path"] in paths


def test_doe_ignores_query_drafts(wiki_env):
    wiki = wiki_env
    path = query_draft_path("proj-doe", "poison")
    # Poisoned bounds in a draft — must not feed DOE
    md = dump_page(
        kind="query",
        title="Poison draft",
        entity_id="query:poison",
        norm_key="poison",
        source_ids=["s1"],
        flags=["unreviewed", "draft"],
        summary="bad",
        bounds=[{"name": "catalyst_wt", "min": 9.0, "max": 99.0, "unit": "wt%"}],
    )
    wiki.upsert_page(
        path=path,
        kind="query",
        title="Poison draft",
        norm_key="poison",
        entity_id="query:poison",
        markdown=md,
        source_ids=["s1"],
        flags=["unreviewed", "draft"],
    )
    assert _is_doe_excluded_page(kind="query", path=path) is True
    bounds = wiki_parameter_bounds()
    assert all(not (b.get("name") == "catalyst_wt" and b.get("min") == 9.0) for b in bounds)
    hints = wiki_doe_hint_lines(["catalyst_wt"])
    assert not any("queries/" in h or "Poison" in h for h in hints)


def test_claims_ignore_wiki_draft_evidence(wiki_env):
    wiki_ev = Evidence(
        title="draft",
        snippet="from wiki draft",
        source="wiki",
        identifier="wiki:queries/project-x-y.md",
        relevance=0.5,
        url=None,
    )
    raw_ev = Evidence(
        title="paper",
        snippet="from raw",
        source="literature",
        identifier="doi:10.1/x",
        relevance=0.9,
        url=None,
    )
    assert is_wiki_evidence(wiki_ev)
    filtered = filter_raw_evidence([wiki_ev, raw_ev])
    assert len(filtered) == 1
    assert filtered[0].source == "literature"
    # build_sourced_claims with claim check off returns None — still ensure filter used upstream
    out = build_sourced_claims("q", "a", [wiki_ev, raw_ev])
    assert out is None or all(
        not (c.chunk_ids and any(str(i).startswith("wiki:") for i in c.chunk_ids))
        for c in (out or [])
    )
