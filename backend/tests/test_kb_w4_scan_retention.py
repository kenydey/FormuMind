"""W4: scan-pressure stats + flag-gated archived retention purge."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.source_store import SourceStore
from app.main import app
from app.services import kb_index
from app.services.kb_retention import purge_expired_archived


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

    db = tmp_path / "w4.db"
    engine = make_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_mod, "_store", src)
    monkeypatch.setattr(chunk_mod, "_store", chk)
    get_settings.cache_clear()
    return src, chk


def _seed(
    src: SourceStore,
    chk: ChunkStore,
    *,
    project_id: str = "p-w4",
    title: str = "W4 epoxy",
    hash_suffix: str = "1",
) -> str:
    sid = src.create(
        filename=f"{title}.pdf",
        title=title,
        source_kind="paper",
        full_text="epoxy resin zinc phosphate " * 40,
        content_hash=f"h-w4-{hash_suffix}",
        origin_url=f"10.1000/w4-{hash_suffix}",
        project_id=project_id,
    )
    chk.replace_for_source(
        sid,
        [{"text": "epoxy resin zinc phosphate coating " * 15}],
    )
    return sid


def test_stats_split_active_archived_and_scan_pressure(env, monkeypatch):
    src, chk = env
    active_id = _seed(src, chk, hash_suffix="a")
    archived_id = _seed(src, chk, title="Archived paper", hash_suffix="b")
    src.set_archived(archived_id, True)

    monkeypatch.setenv("FORMUMIND_KB_SEARCH_SCAN_LIMIT", "10")
    get_settings.cache_clear()

    stats = kb_index.kb_stats()
    assert stats["sources"] == 2
    assert stats["sources_active"] == 1
    assert stats["sources_archived"] == 1
    assert stats["chunks"] == 2
    assert stats["chunks_active"] == 1
    assert stats["chunks_archived"] == 1
    assert stats["scan_limit"] == 10
    assert stats["scan_pressure"] == pytest.approx(0.1)
    assert stats["scan_near_cap"] is False
    assert stats["archive_retention_days"] == 0
    assert stats["suppliers_json_dual_write"] is True

    # Near-cap when active chunks approach scan_limit.
    monkeypatch.setenv("FORMUMIND_KB_SEARCH_SCAN_LIMIT", "1")
    get_settings.cache_clear()
    stats2 = kb_index.kb_stats()
    assert stats2["chunks_active"] == 1
    assert stats2["scan_limit"] == 1
    assert stats2["scan_pressure"] == pytest.approx(1.0)
    assert stats2["scan_near_cap"] is True

    # Orphan chunk counts as active (OUTER JOIN parity with retrieval).
    chk.replace_for_source("orphan-w4", [{"text": "orphan active chunk " * 10}])
    get_settings.cache_clear()
    stats3 = kb_index.kb_stats()
    assert stats3["chunks_active"] == 2
    assert stats3["chunks_archived"] == 1
    assert src.get(active_id) is not None


def test_stats_api_exposes_w4_fields(env):
    src, chk = env
    sid = _seed(src, chk)
    src.set_archived(sid, True)
    client = TestClient(app)
    body = client.get("/api/kb/stats").json()
    assert body["sources_archived"] == 1
    assert body["sources_active"] == 0
    assert body["chunks_archived"] == 1
    assert "scan_pressure" in body
    assert "scan_near_cap" in body
    assert "suppliers_json_dual_write" in body


def test_list_archived_expired_uses_archived_at(env):
    src, chk = env
    sid = _seed(src, chk)
    src.set_archived(sid, True)
    row = src.get(sid)
    assert row is not None and row.archived_at is not None

    # Fresh archive → not expired for days=1
    assert src.list_archived_expired(days=1) == []

    old = datetime.now(timezone.utc) - timedelta(days=10)
    with src._session_factory() as session:
        from app.db.models import SourceDocument

        doc = session.get(SourceDocument, sid)
        assert doc is not None
        doc.archived_at = old.replace(tzinfo=None)
        session.commit()

    expired = src.list_archived_expired(days=7)
    assert len(expired) == 1 and expired[0].id == sid


def test_purge_dry_run_default_and_confirm_required(env):
    src, chk = env
    sid = _seed(src, chk)
    src.set_archived(sid, True)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    with src._session_factory() as session:
        from app.db.models import SourceDocument

        doc = session.get(SourceDocument, sid)
        doc.archived_at = old.replace(tzinfo=None)
        session.commit()

    dry = purge_expired_archived(days=7)
    assert dry["dry_run"] is True
    assert dry["candidate_count"] == 1
    assert dry["purged_count"] == 0
    assert src.get(sid) is not None

    # confirm without flipping dry_run still dry-runs
    still = purge_expired_archived(days=7, confirm=True, dry_run=True)
    assert still["dry_run"] is True
    assert src.get(sid) is not None

    # dry_run=False without confirm still dry-runs
    no_confirm = purge_expired_archived(days=7, confirm=False, dry_run=False)
    assert no_confirm["dry_run"] is True
    assert src.get(sid) is not None

    real = purge_expired_archived(days=7, confirm=True, dry_run=False)
    assert real["dry_run"] is False
    assert real["purged_count"] == 1
    assert sid in real["purged"]
    assert src.get(sid) is None
    assert chk.get_by_source(sid) == []


def test_purge_rejects_days_lt_1():
    with pytest.raises(ValueError, match="days"):
        purge_expired_archived(days=0)


def test_retention_purge_api(env):
    src, chk = env
    sid = _seed(src, chk)
    src.set_archived(sid, True)
    old = datetime.now(timezone.utc) - timedelta(days=40)
    with src._session_factory() as session:
        from app.db.models import SourceDocument

        doc = session.get(SourceDocument, sid)
        doc.archived_at = old.replace(tzinfo=None)
        session.commit()

    client = TestClient(app)

    bad = client.post("/api/kb/retention/purge", json={"days": 0})
    assert bad.status_code == 422

    dry = client.post("/api/kb/retention/purge", json={"days": 14})
    assert dry.status_code == 200
    body = dry.json()
    assert body["dry_run"] is True
    assert body["candidate_count"] == 1
    assert body["candidates"][0]["source_id"] == sid
    assert src.get(sid) is not None

    purged = client.post(
        "/api/kb/retention/purge",
        json={"days": 14, "confirm": True, "dry_run": False},
    )
    assert purged.status_code == 200
    out = purged.json()
    assert out["dry_run"] is False
    assert out["purged_count"] == 1
    assert src.get(sid) is None


def test_purge_skips_active_sources(env):
    src, chk = env
    sid = _seed(src, chk)
    # Never archived — must not appear even with ancient created_at.
    result = purge_expired_archived(days=1, confirm=True, dry_run=False)
    assert result["candidate_count"] == 0
    assert src.get(sid) is not None
