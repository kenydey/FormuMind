"""W3: KB soft-archive — hide from lists/retrieval, keep chunks."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.main import app


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_mod
    import app.db.source_store as source_mod

    db = tmp_path / "arch.db"
    engine = make_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_mod, "_store", src)
    monkeypatch.setattr(chunk_mod, "_store", chk)
    get_settings.cache_clear()
    return src, chk


def _seed(src: SourceStore, chk: ChunkStore, *, project_id: str = "p-w3") -> str:
    sid = src.create(
        filename="epoxy.pdf",
        title="Epoxy corrosion primer",
        source_kind="paper",
        full_text="epoxy resin zinc phosphate " * 40,
        content_hash="h-arch-1",
        origin_url="10.1000/arch1",
        project_id=project_id,
    )
    chk.replace_for_source(
        sid,
        [{"text": "epoxy resin zinc phosphate coating " * 15}],
    )
    return sid


def test_archive_hides_from_list_and_all_chunks(env):
    src, chk = env
    sid = _seed(src, chk)

    assert {r.id for r in src.list_for_project("p-w3")} == {sid}
    assert len(chk.all_chunks(project_id="p-w3")) == 1
    assert len(chk.all_chunks()) == 1

    assert src.set_archived(sid, True) is True
    row = src.get(sid)
    assert row is not None and row.archived is True
    assert len(chk.get_by_source(sid)) == 1, "chunks must remain after soft-archive"

    assert src.list_for_project("p-w3") == []
    archived = src.list_for_project("p-w3", include_archived=True)
    assert len(archived) == 1 and archived[0].archived is True

    assert chk.all_chunks(project_id="p-w3") == []
    assert chk.all_chunks() == []
    assert len(chk.all_chunks(include_archived=True)) == 1


def test_unarchive_restores_list_and_retrieval(env):
    src, chk = env
    sid = _seed(src, chk)
    src.set_archived(sid, True)
    src.set_archived(sid, False)
    assert {r.id for r in src.list_for_project("p-w3")} == {sid}
    assert len(chk.all_chunks(project_id="p-w3")) == 1


def test_archive_api_roundtrip(env):
    src, chk = env
    sid = _seed(src, chk)
    client = TestClient(app)

    r = client.get("/api/kb/sources", params={"project_id": "p-w3"})
    assert r.status_code == 200
    assert {s["id"] for s in r.json()["sources"]} == {sid}

    ar = client.post(f"/api/kb/sources/{sid}/archive", json={"archived": True})
    assert ar.status_code == 200
    assert ar.json() == {"ok": True, "source_id": sid, "archived": True}

    r2 = client.get("/api/kb/sources", params={"project_id": "p-w3"})
    assert r2.json()["sources"] == []

    r3 = client.get(
        "/api/kb/sources",
        params={"project_id": "p-w3", "include_archived": True},
    )
    body = r3.json()["sources"]
    assert len(body) == 1 and body[0]["archived"] is True

    ur = client.post(f"/api/kb/sources/{sid}/archive", json={"archived": False})
    assert ur.status_code == 200 and ur.json()["archived"] is False
    assert {
        s["id"] for s in client.get("/api/kb/sources", params={"project_id": "p-w3"}).json()["sources"]
    } == {sid}


def test_archive_missing_source_404(env):
    client = TestClient(app)
    r = client.post("/api/kb/sources/no-such/archive", json={"archived": True})
    assert r.status_code == 404


def test_orphan_chunks_remain_searchable_without_source_row(env):
    """Chem-QA fixtures seed chunks without SourceDocument — must not INNER JOIN them away."""
    _, chk = env
    chk.replace_for_source(
        "orphan-src",
        [{"text": "磷酸锌缓蚀颜料在环氧底漆中的作用机制与用量。"}],
    )
    assert len(chk.all_chunks()) == 1
    assert len(chk.all_chunks(include_archived=True)) == 1


def test_quota_still_counts_archived(env):
    src, chk = env
    sid = _seed(src, chk)
    assert src.count_for_project("p-w3") == 1
    src.set_archived(sid, True)
    assert src.count_for_project("p-w3") == 1
