"""W0–W2 LLM Wiki: store, compile, API, index_source dispatch."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductMention, SourceGuideSchema
from app.main import app
from app.services.wiki import compile as wiki_compile
from app.services.wiki.schema import safe_key


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    """Isolated Source + Chunk + Wiki stores on one temp SQLite + wiki root."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_WIKI_COMPILE_ON_INGEST", "true")
    get_settings.cache_clear()
    return src, chk, wiki, wiki_root


def _guide_with_product(trade: str = "E-51") -> SourceGuideSchema:
    return SourceGuideSchema(
        summary="环氧树脂配方摘要",
        key_entities=["E-51", "25068-38-6"],
        faqs=["固化条件？"],
        products=[
            ProductMention(trade_name=trade, grade="E51", supplier="本地", role="树脂"),
        ],
        status="verified",
    )


# ── store ────────────────────────────────────────────────────────────────────


def test_wiki_store_upsert_list_hash_idempotent(wiki_env):
    _, _, wiki, root = wiki_env
    md = "---\nkind: material\ntitle: E-51\n---\n# E-51\n"
    row1 = wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown=md,
        source_ids=["s1"],
    )
    assert row1.revision == 1
    assert (root / "materials" / "e51.md").is_file()
    row2 = wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown=md,
        source_ids=["s1", "s2"],
    )
    assert row2.id == row1.id
    assert "s2" in (row2.source_ids or [])
    # same body hash → revision stays
    assert row2.revision == 1
    pages = wiki.list_pages(kind="material")
    assert len(pages) == 1
    assert pages[0].path == "materials/e51.md"


def test_safe_key_rejects_path_traversal():
    assert ".." not in safe_key("../../etc/passwd")
    assert "/" not in safe_key("a/b")
    assert safe_key("E-51®") in {"e-51", "e51"}
    assert "催化" in safe_key("催化剂过量")


# ── compile ──────────────────────────────────────────────────────────────────


def test_wiki_compile_from_source_guide(wiki_env):
    src, _, wiki, root = wiki_env
    sid = src.create(
        filename="p.pdf",
        title="环氧专利",
        source_kind="patent",
        full_text="# body\n" + ("环氧树脂 " * 40),
        content_hash="h1",
        source_guide=_guide_with_product("E-51"),
        origin_url="https://example.com/p1",
    )
    result = wiki_compile.compile_source(sid)
    assert result["ok"] is True
    assert result["materials"] >= 1
    assert any(p.startswith("materials/") for p in result["pages"])
    page = wiki.get_by_path("materials/e51.md")
    assert page is not None
    assert sid in (page.source_ids or [])
    md = wiki.read_markdown(page.path) or ""
    assert f"### Source `{sid}`" in md
    assert "Evidence" in md
    assert (root / "materials" / "e51.md").is_file()


def test_wiki_compile_idempotent_evidence(wiki_env):
    src, _, wiki, _ = wiki_env
    sid = src.create(
        filename="p.pdf",
        title="环氧专利",
        source_kind="patent",
        full_text="# body\n" + ("环氧树脂 " * 40),
        content_hash="h2",
        source_guide=_guide_with_product("E-51"),
    )
    wiki_compile.compile_source(sid)
    wiki_compile.compile_source(sid)
    md = wiki.read_markdown("materials/e51.md") or ""
    assert md.count(f"### Source `{sid}`") == 1


def test_wiki_compile_from_chunk_meta_without_guide(wiki_env):
    src, chk, wiki, _ = wiki_env
    sid = src.create(
        filename="raw.txt",
        title="无 guide",
        source_kind="upload",
        full_text="# body\n" + ("材料 " * 40),
        content_hash="h3",
        source_guide=None,
    )
    chk.replace_for_source(
        sid,
        [
            {
                "text": "E51 epoxy " * 20,
                "meta": {
                    "products": [{"trade_name": "DER-331", "grade": "", "supplier": ""}],
                    "chem": [{"type": "cas", "value": "25068-38-6"}],
                },
            }
        ],
    )
    result = wiki_compile.compile_source(sid)
    assert result["ok"] is True
    assert wiki.get_by_path("materials/der331.md") is not None
    chem = wiki.get_by_path("chemicals/25068-38-6.md")
    assert chem is not None
    assert sid in (chem.source_ids or [])


# ── API ───────────────────────────────────────────────────────────────────────


def test_wiki_api_list_and_detail(wiki_env, monkeypatch):
    _, _, wiki, _ = wiki_env
    wiki.upsert_page(
        path="materials/e51.md",
        kind="material",
        title="E-51",
        norm_key="e51",
        entity_id="material:e51",
        markdown="# E-51\n\n## Summary\nok\n",
        source_ids=["s1"],
    )
    client = TestClient(app)
    r = client.get("/api/wiki/pages")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    page_id = body["pages"][0]["id"]
    d = client.get(f"/api/wiki/pages/{page_id}")
    assert d.status_code == 200
    assert "markdown" in d.json()
    bp = client.get("/api/wiki/by-path", params={"path": "materials/e51.md"})
    assert bp.status_code == 200
    assert bp.json()["path"] == "materials/e51.md"


def test_wiki_api_disabled_409(wiki_env, monkeypatch):
    monkeypatch.setenv("FORMUMIND_WIKI_ENABLED", "false")
    get_settings.cache_clear()
    client = TestClient(app)
    assert client.get("/api/wiki/pages").status_code == 409


# ── index_source dispatch ─────────────────────────────────────────────────────


def test_index_source_dispatches_wiki(wiki_env, monkeypatch):
    from app.services import kb_index

    called: list[str] = []

    def _fake(sid: str):
        called.append(sid)
        return "task-1"

    monkeypatch.setattr("app.worker.tasks.dispatch_wiki_compile", _fake)
    # Also patch the lazy import path used inside index_source
    import app.worker.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "dispatch_wiki_compile", _fake)

    n = kb_index.index_source("src-wiki-1", "# Title\n\n" + ("环氧树脂配方 " * 50), embed=False)
    assert n > 0
    assert called == ["src-wiki-1"]


def test_index_source_no_dispatch_when_disabled(wiki_env, monkeypatch):
    from app.services import kb_index

    monkeypatch.setenv("FORMUMIND_WIKI_COMPILE_ON_INGEST", "false")
    get_settings.cache_clear()
    called: list[str] = []

    def _fake(sid: str):
        called.append(sid)
        return "task-x"

    import app.worker.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "dispatch_wiki_compile", _fake)
    n = kb_index.index_source("src-wiki-2", "# Title\n\n" + ("环氧树脂配方 " * 50), embed=False)
    assert n > 0
    assert called == []


def test_dispatch_wiki_compile_eager_runs(wiki_env, monkeypatch):
    """Eager path schedules a daemon thread; compile should land shortly."""
    from app.worker import tasks as tasks_mod

    src, _, wiki, _ = wiki_env
    sid = src.create(
        filename="p.pdf",
        title="eager",
        source_kind="patent",
        full_text="# body\n" + ("环氧 " * 40),
        content_hash="he",
        source_guide=_guide_with_product("E-51"),
    )
    monkeypatch.setenv("FORMUMIND_CELERY_EAGER", "true")
    get_settings.cache_clear()
    tid = tasks_mod.dispatch_wiki_compile(sid)
    assert tid and tid.startswith("wikicompile-")
    deadline = time.time() + 5
    while time.time() < deadline:
        if wiki.get_by_path("materials/e51.md") is not None:
            break
        time.sleep(0.05)
    assert wiki.get_by_path("materials/e51.md") is not None
