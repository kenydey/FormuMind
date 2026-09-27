"""Tests for W3-5 (P1-36/37): paper detail aggregation + batch export API."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ingest as ingest_api
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import DocumentChunk, SourceDocument
from app.db.source_store import SourceStore
from app.services import provenance as prov_svc
from app.services import table_contract
from app.services.table_contract import TableAsset


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/w35_test.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _add_source(sf, sid, **kw):
    with sf() as s:
        s.add(
            SourceDocument(
                id=sid,
                filename=kw.get("filename", f"{sid}.pdf"),
                title=kw.get("title", f"Title {sid}"),
                source_kind=kw.get("source_kind", "local"),
                content_hash=kw.get("content_hash", "h" * 64),
                full_text=kw.get("full_text", "全文内容 " * 50),
                raw_text_chars=kw.get("raw_text_chars", 500),
                source_guide=kw.get("source_guide"),
            )
        )
        for i, ch in enumerate(kw.get("chunks", [])):
            s.add(
                DocumentChunk(
                    id=f"{sid}-c{i}",
                    source_id=sid,
                    ord=i,
                    text=ch.get("text", f"chunk {i}"),
                    page_no=ch.get("page_no"),
                    heading_path=ch.get("heading_path", ""),
                )
            )
        s.commit()


@pytest.fixture()
def client(sf, tmp_path, monkeypatch):
    _add_source(
        sf,
        "s1",
        chunks=[
            {"text": "第一章内容", "page_no": 1, "heading_path": "引言"},
            {"text": "第二章内容", "page_no": 3, "heading_path": "方法"},
        ],
        source_guide={"summary": "摘要文本", "key_entities": ["环氧树脂"]},
    )
    _add_source(sf, "s2", full_text=None, chunks=[])
    monkeypatch.setattr(ingest_api, "get_source_store", lambda: SourceStore(sf))
    monkeypatch.setenv("FORMUMIND_TABLES_DIR", str(tmp_path / "tables"))
    table_contract.save_tables(
        "s1",
        [
            TableAsset(
                table_id="s1#p01-00",
                source_id="s1",
                page_no=1,
                caption="表1 配方",
                provenance={"parser": "docling"},
                kind="recipe",
                headers=["成分", "份数"],
                rows=[["环氧树脂", "100"], ["固化剂", "40"]],
                raw_markdown="| 成分 | 份数 |",
            )
        ],
    )
    monkeypatch.setattr(
        prov_svc,
        "lineage",
        lambda node_type, node_id, **kw: [
            {
                "from_type": "claim",
                "from_id": "cl1",
                "to_type": "source",
                "to_id": node_id,
                "relation": "supports",
            }
        ],
    )
    app = FastAPI()
    app.include_router(ingest_api.router)
    return TestClient(app)


# ── P1-36 detail ────────────────────────────────────────────────────────────


def test_detail_full_aggregation(client):
    r = client.get("/sources/s1/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "s1"
    assert body["title"] == "Title s1"
    assert body["has_fulltext"] is True
    assert body["total_chunks"] == 2
    assert body["chunks_truncated"] is False
    assert [c["page_no"] for c in body["chunks"]] == [1, 3]
    assert body["chunks"][0]["heading_path"] == "引言"
    assert len(body["tables"]) == 1
    t = body["tables"][0]
    assert t["table_id"] == "s1#p01-00"
    assert t["kind"] == "recipe"
    assert t["n_rows"] == 2 and t["n_cols"] == 2
    assert t["property_set"] is None  # W3-1 not implemented yet → fail-open None
    assert body["provenance_upstream"][0]["from_id"] == "cl1"


def test_detail_404_unknown(client):
    r = client.get("/sources/nope/detail")
    assert r.status_code == 404


def test_detail_chunks_fail_open(client, monkeypatch):
    def boom(store):
        raise RuntimeError("db down")

    monkeypatch.setattr(ingest_api, "_detail_session_factory", boom)
    r = client.get("/sources/s1/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["chunks"] == [] and body["total_chunks"] == 0
    # other sections still served
    assert len(body["tables"]) == 1
    assert body["has_fulltext"] is True


def test_detail_missing_tables_and_lineage_fail_open(client, monkeypatch):
    monkeypatch.setattr(prov_svc, "lineage", lambda *a, **k: [])
    r = client.get("/sources/s2/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["tables"] == []
    assert body["provenance_upstream"] == []
    assert body["chunks"] == [] and body["total_chunks"] == 0
    assert body["has_fulltext"] is False


# ── P1-37 export ────────────────────────────────────────────────────────────


def test_export_md_ok(client):
    r = client.post(
        "/sources/export", json={"source_ids": ["s1", "s2"], "format": "md"}
    )
    assert r.status_code == 200
    text = r.content.decode("utf-8")
    assert "文献批量导出" in text
    assert "Title s1" in text and "Title s2" in text
    assert "摘要文本" in text  # source_guide summary included
    assert "引用清单" in text
    assert "s1#p01-00" not in text  # table ids not leaked, only summary counts
    assert "共 1 张" in text
    assert 'attachment; filename="sources_export.md"' in r.headers["content-disposition"]


def test_export_skips_missing_ids(client):
    r = client.post("/sources/export", json={"source_ids": ["s1", "ghost"], "format": "md"})
    assert r.status_code == 200
    assert "1 篇未找到已跳过" in r.content.decode("utf-8")


def test_export_too_many_400(client):
    r = client.post(
        "/sources/export",
        json={"source_ids": [f"s{i}" for i in range(11)], "format": "md"},
    )
    assert r.status_code == 400


def test_export_empty_and_all_missing(client):
    r = client.post("/sources/export", json={"source_ids": [], "format": "md"})
    assert r.status_code == 400
    r = client.post("/sources/export", json={"source_ids": ["ghost"], "format": "md"})
    assert r.status_code == 404
