"""Multi-modal KG fusion pipeline — vision table → structural graph links."""
from __future__ import annotations

import logging
from pathlib import Path

from ..config import Settings, get_settings
from ..db.chunk_store import get_chunk_store
from ..db.entity_store import get_entity_store
from ..domain.multimodal_schemas import MultimodalFusionResult
from ..services.errors import degrade_return
from ..services.kg.structural_extractor import extract_triples_from_vision_data
from ..services.vision_extract import extract_structured_table_from_image

logger = logging.getLogger(__name__)


def _first_chunk_id(source_id: str) -> str:
    chunks = get_chunk_store().get_by_source(source_id)
    return chunks[0].id if chunks else ""


def persist_structural_extraction(
    extraction,
    *,
    source_id: str,
    chunk_id: str,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    if not settings.kg_enabled:
        return 0
    store = get_entity_store()
    written = 0
    with store._session_factory() as session:
        for fields in extraction.entities.values():
            store.upsert_entity(session, **fields)
        for link in extraction.links:
            evidence_ref = {
                "source_id": source_id,
                "chunk_id": chunk_id or None,
                "sentence": link.evidence_sentence[:500],
                "confidence": link.confidence,
                "extraction_method": "vision_table",
            }
            if store.merge_structural_link(
                session,
                src_entity_id=link.src_entity_id,
                dst_entity_id=link.dst_entity_id,
                link_type=link.link_type.value,
                confidence=link.confidence,
                evidence_ref=evidence_ref,
                metadata=link.metadata,
                extraction_method="vision_table",
            ):
                written += 1
                if chunk_id:
                    store.add_mention(
                        session,
                        entity_id=link.src_entity_id,
                        source_id=source_id,
                        chunk_id=chunk_id,
                        surface_form=link.evidence_sentence[:80],
                        extractor="vision_table",
                        confidence=link.confidence,
                    )
        session.commit()
    if extraction.entities:
        store.refresh_counts(list(extraction.entities.keys()))
    return written


def run_multimodal_kg_fusion(
    image_path: str,
    context: str = "",
    *,
    source_id: str | None = None,
    chunk_id: str | None = None,
    persist: bool = False,
    settings: Settings | None = None,
) -> MultimodalFusionResult:
    """Read image → vision JSON → structural triples → optional KG persist."""
    settings = settings or get_settings()
    result = MultimodalFusionResult()

    if not settings.kg_multimodal_fusion_enabled and persist:
        result.warnings.append("多模态图谱融合已禁用（FORMUMIND_KG_MULTIMODAL_FUSION_ENABLED）")
        return result

    path = Path(image_path)
    if not path.is_file():
        result.warnings.append(f"图片不存在: {image_path}")
        return result

    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        result.warnings.append(f"读取图片失败: {exc}")
        return result

    return run_multimodal_kg_fusion_bytes(
        image_bytes,
        path.name,
        context,
        source_id=source_id,
        chunk_id=chunk_id,
        persist=persist,
        settings=settings,
    )


def run_multimodal_kg_fusion_bytes(
    image_bytes: bytes,
    filename: str,
    context: str = "",
    *,
    source_id: str | None = None,
    chunk_id: str | None = None,
    persist: bool = False,
    settings: Settings | None = None,
) -> MultimodalFusionResult:
    settings = settings or get_settings()
    result = MultimodalFusionResult()

    if not settings.vision_extract_enabled:
        result.vision_error = "图片视觉解析已禁用"
        return result

    vision, err = extract_structured_table_from_image(image_bytes, filename, context)
    result.vision = vision
    result.vision_error = err

    if vision is None:
        if err:
            result.warnings.append(err)
        return result

    sid = source_id or "local:vision"
    cid = chunk_id or ""
    extraction = extract_triples_from_vision_data(
        vision,
        source_id=sid,
        chunk_id=cid,
        settings=settings,
    )
    result.extraction = extraction
    result.warnings.extend(extraction.warnings)

    if persist and settings.kg_enabled and settings.kg_multimodal_fusion_enabled:
        try:
            if source_id and not chunk_id:
                cid = _first_chunk_id(source_id)
            result.links_written = persist_structural_extraction(
                extraction,
                source_id=sid,
                chunk_id=cid,
                settings=settings,
            )
            result.persisted = result.links_written > 0
        except Exception as exc:
            degrade_return(logger, exc, "multimodal kg persist failed", None)
            result.warnings.append(f"写入图谱失败: {exc}")

    return result
