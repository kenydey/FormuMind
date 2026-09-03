"""Multi-modal KG fusion — vision table → structural links."""
from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.models import KGEntityLink
from app.domain.multimodal_schemas import VisionTableExtraction
from app.pipeline.multimodal_fusion import (
    persist_structural_extraction,
    run_multimodal_kg_fusion_bytes,
)
from app.services.kg.entity_normalizer import resolve_material_surface
from app.services.kg.structural_extractor import extract_triples_from_vision_data
from app.services.kg.prompts import build_multimodal_table_prompt


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/mm_kg.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


SAMPLE_VISION = {
    "table_type": "comparison",
    "confidence": 0.86,
    "formulations": [
        {
            "example_id": "实施例1",
            "substrate": {"name": "Q235 碳钢", "pre_treatment": "喷砂 Sa2.5"},
            "ingredients": [
                {"name": "磷酸锌", "concentration": 8.0, "unit": "wt%", "role": "防锈颜料"},
                {"name": "环氧树脂", "concentration": 20.0, "unit": "wt%"},
            ],
            "performances": [
                {"metric": "NSS", "value": 720, "unit": "h", "conditions": "5% NaCl 35°C"},
            ],
        }
    ],
    "notes": "",
}


def test_build_multimodal_prompt_includes_context():
    prompt = build_multimodal_table_prompt("专利实施例对比表")
    assert "上下文文本" in prompt
    assert "专利实施例对比表" in prompt


def test_extract_triples_from_vision_data(entity_store):
    result = extract_triples_from_vision_data(
        SAMPLE_VISION,
        source_id="src-1",
        chunk_id="chunk-1",
    )
    assert len(result.links) >= 4
    types = {link.link_type.value for link in result.links}
    assert "has_ingredient" in types
    assert "has_performance" in types
    assert "tested_on_substrate" in types
    assert any(eid.startswith("form:") for eid in result.entities)


def test_resolve_material_surface_catalog():
    resolved = resolve_material_surface("Zinc phosphate")
    assert resolved is not None
    assert resolved.entity_id.startswith("chem:catalog:")


def test_malformed_vision_json_returns_warnings():
    result = extract_triples_from_vision_data({"formulations": "not-a-list"}, source_id="s1")
    assert result.links == []
    assert result.warnings


def test_persist_structural_extraction(entity_store, monkeypatch):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    extraction = extract_triples_from_vision_data(SAMPLE_VISION, source_id="src-p", chunk_id="c1")
    written = persist_structural_extraction(extraction, source_id="src-p", chunk_id="c1")
    assert written >= 4
    with entity_store._session_factory() as session:
        rows = session.query(KGEntityLink).all()
        assert len(rows) >= 4
        assert any(r.link_type == "has_ingredient" for r in rows)


def test_run_multimodal_pipeline_mock_vision(monkeypatch, entity_store, tmp_path):
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KG_MULTIMODAL_FUSION_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_VISION_EXTRACT_ENABLED", "true")
    get_settings.cache_clear()

    img = tmp_path / "table.png"
    img.write_bytes(b"\x89PNG fake")

    def fake_extract(_bytes, filename, context=""):
        return VisionTableExtraction.model_validate(SAMPLE_VISION), None

    monkeypatch.setattr(
        "app.pipeline.multimodal_fusion.extract_structured_table_from_image",
        fake_extract,
    )

    result = run_multimodal_kg_fusion_bytes(
        img.read_bytes(),
        img.name,
        context="对比表",
        source_id="src-mm",
        chunk_id="c-mm",
        persist=True,
    )
    assert result.vision is not None
    assert result.extraction is not None
    assert result.persisted
    assert result.links_written >= 4


def test_vision_table_invalid_json_returns_none(monkeypatch):
    from app.services import vision_extract

    monkeypatch.setenv("FORMUMIND_VISION_EXTRACT_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(vision_extract, "vision_available", lambda: (True, ""))
    monkeypatch.setattr(
        vision_extract,
        "_call_openai_vision",
        lambda *a, **k: "not json at all",
    )
    monkeypatch.setattr(vision_extract, "effective_setting", lambda s, k: "openai")
    monkeypatch.setattr(
        "app.services.vision_extract.get_settings",
        lambda: type("S", (), {
            "get_active_api_key": lambda self: "k",
            "llm_timeout_seconds": 30,
            "llm_max_tokens": 1024,
            "vision_model": "gpt-4o",
        })(),
    )

    extraction, err = vision_extract.extract_structured_table_from_image(b"x", "t.png")
    assert extraction is None
    assert err
