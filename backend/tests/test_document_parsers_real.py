"""A real datasheet, from upload to the knowledge base (round-4).

Everything else about ingestion is tested with hand-written markdown or a mocked parser. These tests put an
actual ``.docx`` / ``.xlsx`` through ``POST /api/ingest`` — real converter, real chunker, real quality gate, real
sidecar, real retrieval — and check what a user would ask afterwards: *is the table in the knowledge base, with
its caption, its header and its units?*

Before round 4 the answer was no, three different ways (a Word table lost its header row, the quality gate
dropped every pipe table as "garbage", the caption was cut loose from its table). They skip when the parser
extras (``file_ingest``) are not installed — the CI job ``backend-parsers`` installs them.
"""
from __future__ import annotations

import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

pytest.importorskip("markitdown")

ROWS = [("项目", "指标", "单位"), ("固体含量", "65", "%"), ("粘度", "1200", "mPa·s"), ("耐盐雾性能", "720", "h")]
TITLE = "环氧锌磷酸盐底漆 技术数据表（TDS）"
CAPTION = "表1 典型性能"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """One isolated database for the ingest job (a daemon thread) and for the assertions.

    The source / chunk stores are process singletons bound to whichever database was current when they were first
    used, so a per-test ``FORMUMIND_DB_URL`` alone would leave the job writing into the first test's file.
    """
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore
    from app.main import app

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "true")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/real_docs.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(source_store_mod, "_store", SourceStore(factory))
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    yield TestClient(app)
    get_settings.cache_clear()


def _docx_bytes() -> bytes:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_heading(TITLE, 1)
    doc.add_paragraph(CAPTION)
    table = doc.add_table(rows=len(ROWS), cols=3)
    for i, row in enumerate(ROWS):
        for j, value in enumerate(row):
            table.cell(i, j).text = value
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TDS"
    for row in ROWS:
        ws.append([row[0], float(row[1]) if row[1].replace(".", "").isdigit() else row[1], row[2]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ingest(client: TestClient, name: str, content: bytes):
    """Upload, then wait for the background job (Celery-eager runs it in a daemon thread) to *finish*.

    The source row appears before its chunks are written, so "the row exists" is not "the job is done" — waiting on
    the row alone made this flaky under load (an empty chunk list read between the two writes).
    """
    from app.db.database import default_session_factory
    from app.db.models import DocumentChunk, SourceDocument

    resp = client.post("/api/ingest", files={"file": (name, content)})
    assert resp.status_code == 202, resp.text
    status_url = resp.json()["status_url"]
    deadline = time.monotonic() + 90
    state = ""
    while time.monotonic() < deadline:
        state = client.get(status_url).json().get("state", "")
        if state in ("completed", "failed", "error"):
            break
        time.sleep(0.2)
    assert state == "completed", f"{name}: ingest job ended in state {state!r}"
    with default_session_factory()() as session:
        source = session.query(SourceDocument).filter(SourceDocument.filename == name).one()
        chunks = session.query(DocumentChunk).filter(DocumentChunk.source_id == source.id).all()
        session.expunge_all()
        return source, chunks


def test_a_docx_datasheet_table_is_stored_with_its_caption(client):
    source, chunks = _ingest(client, "tds.docx", _docx_bytes())
    tables = [c for c in chunks if c.block_type == "table"]
    assert len(tables) == 1, [c.text[:40] for c in chunks]
    text = tables[0].text
    assert text.startswith(CAPTION), "the caption must travel with its table"
    assert "耐盐雾性能" in text and "720" in text


def test_a_docx_datasheet_is_normalised_with_units(client):
    source, _ = _ingest(client, "tds.docx", _docx_bytes())
    detail = client.get(f"/api/sources/{source.id}/detail").json()
    (table,) = detail["tables"]
    assert table["kind"] == "performance"
    props = {p["name"]: (p["name_normalized"], p["value"], p["unit_normalized"]) for p in table["property_set"]["properties"]}
    assert props == {
        "固体含量": ("solids_content", 65.0, "%"),
        "粘度": ("viscosity", 1200.0, "mPa.s"),
        "耐盐雾性能": ("salt_spray", 720.0, "h"),
    }


def test_the_table_answers_a_search_for_its_content(client):
    _ingest(client, "tds.docx", _docx_bytes())
    hits = client.get("/api/kb/search", params={"q": "耐盐雾性能 720", "k": 3}).json()["results"]
    assert hits, "a datasheet's own table must be findable by what is in it"
    assert any("720" in (h.get("snippet") or "") for h in hits)


def test_an_xlsx_datasheet_reaches_the_knowledge_base(client):
    source, chunks = _ingest(client, "tds.xlsx", _xlsx_bytes())
    assert any("固体含量" in c.text for c in chunks), [c.text[:40] for c in chunks]
    detail = client.get(f"/api/sources/{source.id}/detail").json()
    assert [t["kind"] for t in detail["tables"]] == ["performance"], json.dumps(detail["tables"], ensure_ascii=False)[:300]
