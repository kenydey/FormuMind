"""P3.2 — Evidence one-click fulltext ingest + patent id normalize."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import Evidence
from app.services import fulltext_fetcher as ff
from app.services import kb_ingest
from app.services.patent_ids import (
    canonical_origin_url,
    normalize_patent_pub,
    patent_id_aliases,
)


LONG_TEXT = "# 防腐蚀专利\n\n" + "环氧树脂与磷酸锌协同防腐蚀机理研究。" * 60


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb_p32.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    return src, chk


def _ev(**kw) -> Evidence:
    base = dict(
        source="surechembl",
        identifier="CN-104789083-B",
        title="Primer coating",
        snippet="chemistry",
        relevance=0.9,
        url="https://patents.google.com/patent/CN104789083B",
    )
    base.update(kw)
    return Evidence(**base)


def test_normalize_hyphen_compact_url():
    office, compact = normalize_patent_pub("CN-104789083-B")
    assert office == "CN"
    assert compact == "CN104789083B"
    assert normalize_patent_pub("CN104789083B")[1] == "CN104789083B"
    assert normalize_patent_pub("https://patents.google.com/patent/CN104789083B")[1] == "CN104789083B"
    aliases = patent_id_aliases("CN-104789083-B")
    assert "CN104789083B" in aliases
    assert "CN-104789083-B" in aliases
    assert any("patents.google.com" in a for a in aliases)
    assert canonical_origin_url("CN-104789083-B") == "CN104789083B"


def test_classify_surechembl_hyphenated():
    assert ff.classify(_ev()) == "patent"
    assert ff.classify(_ev(identifier="CN104789083B", url=None)) == "patent"
    assert (
        ff.classify(
            _ev(
                identifier="doc-without-pub",
                url="https://patents.google.com/patent/CN104789083B",
            )
        )
        == "patent"
    )


def test_select_targets_includes_surechembl(monkeypatch):
    monkeypatch.setattr(get_settings(), "kb_ingest_max_docs", 0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_min_relevance", 0.0, raising=False)
    monkeypatch.setattr(get_settings(), "kb_ingest_topic_filter", False, raising=False)
    targets = kb_ingest.select_ingest_targets([_ev()])
    assert len(targets) == 1
    assert targets[0][1] == "patent"


def test_ingest_evidence_mock_success_and_skip(stores, monkeypatch):
    def fake_dispatch(kind, ev, timeout):
        assert kind == "patent"
        assert normalize_patent_pub(ev.identifier)[1] == "CN104789083B"
        return LONG_TEXT

    monkeypatch.setattr(ff, "_dispatch_fetch", fake_dispatch)
    monkeypatch.setattr("app.services.kb_index.index_source", lambda *a, **k: 1)

    from app.main import app

    client = TestClient(app)
    r1 = client.post(
        "/api/kb/ingest-evidence",
        json={
            "identifier": "CN-104789083-B",
            "title": "Primer",
            "source": "surechembl",
            "url": "https://patents.google.com/patent/CN104789083B",
        },
    )
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["ok"] is True
    assert body["status"] == "indexed"
    assert body["source_id"]
    assert body["canonical_id"] == "CN104789083B"
    sid = body["source_id"]

    # Persist used compact origin_url
    src, _ = stores
    doc = src.find_by_origin_url("CN104789083B")
    assert doc is not None and doc.id == sid

    r2 = client.post(
        "/api/kb/ingest-evidence",
        json={
            "identifier": "CN104789083B",
            "title": "Primer",
            "source": "surechembl",
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["ok"] is True
    assert body2["status"] == "skipped"
    assert body2["source_id"] == sid


def test_ingest_evidence_unsupported():
    from app.main import app

    client = TestClient(app)
    r = client.post(
        "/api/kb/ingest-evidence",
        json={"identifier": "just a title", "source": "notebooklm"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "failed"
    assert "不支持" in (body["reason"] or "")


def test_dedup_aliases_via_source_store(stores):
    src, _ = stores
    sid = src.create(
        filename="CN104789083B",
        title="t",
        source_kind="patent",
        full_text="x" * 300,
        content_hash="abc",
        extraction_status="fulltext",
        origin_url="CN104789083B",
    )
    hit = src.find_by_origin_url("CN-104789083-B")
    assert hit is not None
    assert hit.id == sid
    hit2 = src.find_by_origin_urls(
        ["https://patents.google.com/patent/CN104789083B"]
    )
    assert hit2 is not None and hit2.id == sid


def test_fetch_patent_uses_compact(monkeypatch):
    seen: list[str] = []

    def fake_fetch(pub, timeout=20):
        seen.append(pub)
        return LONG_TEXT

    monkeypatch.setattr(
        "app.services.pdf_downloader.fetch_patent_text", fake_fetch
    )
    text = ff._fetch_patent_text(_ev(), timeout=5)
    assert text and len(text) > 200
    assert seen == ["CN104789083B"]
