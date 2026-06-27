"""Tests for Source Guide extraction, chunking, persistence, and ingest API."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.db.database import make_engine, make_session_factory
from app.db.models import SourceDocument
from app.db.source_store import SourceStore
from app.domain.schemas import ParameterBoundary, SourceGuideSchema
from app.main import app
from app.services import ingestion
from app.services.source_guide import _select_extraction_text, extract_source_guide

client = TestClient(app)

PATENT_FIXTURE = """
本发明涉及一种用于铝合金的无铬转化膜处理液。

摘要：采用六氟锆酸作为成膜剂，在弱酸性条件下于基材表面形成致密转化膜。

实施例1：取六氟锆酸 0.8 g/L，调节 pH 至 4.2，处理温度 30°C，浸渍时间 90 s。
实施例2：Zr 浓度 0.5–1.2 g/L，pH 4.0–4.5，处理 60–120 s，温度 25–40°C。

背景技术：传统铬酸盐转化膜存在环保问题。
"""

TABLE_FIXTURE = """
摘要：本发明涉及表面处理液。

背景技术：传统方法存在环保问题。

| 组分 | 浓度 (g/L) | 温度 (°C) |
| --- | --- | --- |
| 六氟锆酸 | 0.5–1.2 | 25–40 |
| 柠檬酸 | 2.0 | 30 |

实施例1：按上表配制处理液。
"""


def _memory_source_store() -> SourceStore:
    engine = make_engine("sqlite:///:memory:")
    return SourceStore(make_session_factory(engine))


def _sample_guide() -> SourceGuideSchema:
    return SourceGuideSchema(
        summary="含锆无铬转化膜处理液，用于铝合金表面防腐。",
        key_entities=["六氟锆酸 (CAS: 12021-95-3)", "A356 cast aluminum alloy"],
        parameter_space={
            "Zr_concentration": ParameterBoundary(min_value=0.5, max_value=1.2, unit="g/L"),
            "pH_range": ParameterBoundary(min_value=4.0, max_value=4.5, unit=""),
            "treatment_time": ParameterBoundary(min_value=60, max_value=120, unit="seconds"),
            "temperature": ParameterBoundary(min_value=25, max_value=40, unit="°C"),
        },
        faqs=[
            "如何在压铸铝基材上实现120小时以上的中性盐雾性能？",
            "无铬转化膜的成膜机理是什么？",
            "Zr 浓度对膜层质量有何影响？",
        ],
    )


def test_boundary_min_gt_max_raises():
    with pytest.raises(ValidationError):
        ParameterBoundary(min_value=1.5, max_value=0.5, unit="g/L")


def test_boundary_negative_concentration_raises():
    with pytest.raises(ValidationError):
        ParameterBoundary(min_value=-0.1, max_value=1.0, unit="g/L")


def test_boundary_one_side_none_ok():
    boundary = ParameterBoundary(min_value=0.5, max_value=None, unit="g/L")
    assert boundary.min_value == 0.5
    assert boundary.max_value is None


def test_source_guide_schema_degraded_factory():
    guide = SourceGuideSchema.degraded("LLM timeout")
    assert guide.status == "degraded"
    assert guide.parameter_space == {}
    assert len(guide.key_entities) >= 1


def test_select_extraction_text_prioritizes_examples():
    excerpt = _select_extraction_text(PATENT_FIXTURE, max_chars=500)
    assert "实施例" in excerpt
    assert excerpt.index("实施例") < excerpt.find("背景技术") if "背景技术" in excerpt else True


def test_select_text_table_block_boosted():
    excerpt = _select_extraction_text(TABLE_FIXTURE, max_chars=400)
    assert "| --- |" in excerpt or "| 组分 |" in excerpt
    assert excerpt.index("|") < excerpt.find("背景技术") if "背景技术" in excerpt else True


def test_extract_source_guide_mock_llm():
    guide = _sample_guide()
    with patch("app.services.source_guide.complete_structured", return_value=(guide, None)):
        result, err = extract_source_guide(PATENT_FIXTURE, title="patent.pdf")
    assert err is None
    assert result is not None
    assert result.status == "verified"
    zr = result.parameter_space["Zr_concentration"]
    assert zr.min_value == 0.5
    assert zr.max_value == 1.2
    assert zr.unit == "g/L"


def test_extract_degraded_on_validation_fail():
    with patch("app.services.source_guide.complete_structured", return_value=(None, "bad")):
        result, err = extract_source_guide(PATENT_FIXTURE)
    assert result is not None
    assert result.status == "degraded"
    assert err == "bad"


def test_extract_graceful_fail():
    with patch("app.services.source_guide.complete_structured", side_effect=RuntimeError("LLM down")):
        result, err = extract_source_guide(PATENT_FIXTURE)
    assert result is not None
    assert result.status == "degraded"
    assert err is not None


def test_ingest_persists_full_text_and_guide():
    store = _memory_source_store()
    guide = _sample_guide()

    with (
        patch("app.services.ingestion.get_source_store", return_value=store),
        patch("app.services.ingestion.extract_source_guide", return_value=(guide, None)),
        patch("app.services.ingestion.get_settings") as mock_settings,
    ):
        settings = mock_settings.return_value
        settings.source_guide_enabled = True
        settings.get_active_api_key.return_value = "test-key"
        settings.ingest_max_chunks = 40
        settings.ingest_chunk_max_chars = 1600
        settings.ingest_chunk_overlap = 200

        outcome = ingestion._ingest_parsed_text(
            PATENT_FIXTURE,
            filename="patent.pdf",
            source_kind="local",
            persist=True,
        )

    assert outcome.source_id is not None
    assert outcome.extraction_status == "ok"
    assert outcome.source_guide is not None

    with store._session_factory() as session:
        row = session.get(SourceDocument, outcome.source_id)
    assert row is not None
    assert row.full_text == PATENT_FIXTURE
    assert row.source_guide is not None
    assert row.source_guide["parameter_space"]["Zr_concentration"]["min_value"] == 0.5


def test_ingest_persists_degraded_status():
    store = _memory_source_store()
    degraded = SourceGuideSchema.degraded()

    with (
        patch("app.services.ingestion.get_source_store", return_value=store),
        patch("app.services.ingestion.extract_source_guide", return_value=(degraded, "bad")),
        patch("app.services.ingestion.get_settings") as mock_settings,
    ):
        settings = mock_settings.return_value
        settings.source_guide_enabled = True
        settings.get_active_api_key.return_value = "test-key"
        settings.ingest_max_chunks = 40
        settings.ingest_chunk_max_chars = 1600
        settings.ingest_chunk_overlap = 200

        outcome = ingestion._ingest_parsed_text(
            PATENT_FIXTURE,
            filename="patent.pdf",
            source_kind="local",
            persist=True,
        )

    assert outcome.extraction_status == "degraded"
    assert outcome.source_guide is not None
    assert outcome.source_guide.status == "degraded"

    with store._session_factory() as session:
        row = session.get(SourceDocument, outcome.source_id)
    assert row is not None
    assert row.extraction_status == "degraded"
    assert row.source_guide is not None
    assert row.source_guide["status"] == "degraded"


def test_ingest_without_api_key():
    store = _memory_source_store()

    with (
        patch("app.services.ingestion.get_source_store", return_value=store),
        patch("app.services.ingestion.get_settings") as mock_settings,
    ):
        settings = mock_settings.return_value
        settings.source_guide_enabled = True
        settings.get_active_api_key.return_value = None
        settings.ingest_max_chunks = 40
        settings.ingest_chunk_max_chars = 1600
        settings.ingest_chunk_overlap = 200

        outcome = ingestion._ingest_parsed_text(
            PATENT_FIXTURE,
            filename="patent.pdf",
            source_kind="local",
            persist=True,
        )

    assert outcome.extraction_status == "skipped"
    assert outcome.source_guide is None
    assert len(outcome.evidence) >= 1


def test_chunking_not_empty_for_long_patent():
    long_text = PATENT_FIXTURE + "\n\n" + ("详细工艺说明段落。" * 200)
    with patch("app.services.ingestion.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.ingest_max_chunks = 40
        settings.ingest_chunk_max_chars = 400
        settings.ingest_chunk_overlap = 50

        chunks = ingestion._chunk_text(long_text, max_chars=400, overlap=50)
    assert len(chunks) > 1


def test_ingest_text_api_returns_source_fields():
    with patch("app.services.ingestion.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.source_guide_enabled = False
        settings.ingest_max_chunks = 40
        settings.ingest_chunk_max_chars = 1600
        settings.ingest_chunk_overlap = 200

        r = client.post(
            "/api/ingest/text",
            json={"text": PATENT_FIXTURE, "title": "Zr patent"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert "extraction_status" in body
    assert body["extraction_status"] == "skipped"


def test_get_source_endpoint():
    import app.api.ingest as ingest_api

    store = _memory_source_store()
    guide = _sample_guide()
    source_id = store.create(
        filename="patent.pdf",
        title="patent",
        source_kind="local",
        full_text=PATENT_FIXTURE,
        content_hash="abc123",
        source_guide=guide,
        extraction_status="ok",
    )

    original = ingest_api.get_source_store
    ingest_api.get_source_store = lambda: store
    try:
        resp = ingest_api.get_source(source_id)
    finally:
        ingest_api.get_source_store = original

    assert resp.id == source_id
    assert resp.source_guide is not None
    assert resp.source_guide.parameter_space["Zr_concentration"].min_value == 0.5


def test_get_source_not_found():
    import app.api.ingest as ingest_api
    from fastapi import HTTPException

    store = _memory_source_store()
    original = ingest_api.get_source_store
    ingest_api.get_source_store = lambda: store
    try:
        with pytest.raises(HTTPException) as exc_info:
            ingest_api.get_source("missing-id")
        assert exc_info.value.status_code == 404
    finally:
        ingest_api.get_source_store = original
