"""Convert vision table JSON into KG structural formulation links."""
from __future__ import annotations

import logging

from ...config import Settings, get_settings
from ...domain.multimodal_schemas import (
    FormulationLink,
    StructuralExtractionResult,
    StructuralLinkType,
    VisionTableExtraction,
)
from .entity_normalizer import (
    formulation_entity_id,
    resolve_material_surface,
    resolve_metric_surface,
    resolve_substrate_surface,
    resolved_to_upsert_fields,
)

logger = logging.getLogger(__name__)


def extract_triples_from_vision_data(
    vision_json: dict | VisionTableExtraction,
    *,
    source_id: str,
    chunk_id: str = "",
    settings: Settings | None = None,
) -> StructuralExtractionResult:
    """Map vision table extraction to formulation structural links."""
    settings = settings or get_settings()
    result = StructuralExtractionResult()

    try:
        if isinstance(vision_json, VisionTableExtraction):
            vision = vision_json
        else:
            vision = VisionTableExtraction.model_validate(vision_json)
    except Exception as exc:
        result.warnings.append(f"vision_json 校验失败: {exc}")
        return result

    if not vision.formulations:
        result.warnings.append("未识别到任何实施例/配方行")
        return result

    base_conf = max(0.0, min(1.0, float(vision.confidence or 0.7)))

    for formulation in vision.formulations:
        example_id = (formulation.example_id or "实施例").strip()
        form_id = formulation_entity_id(source_id, example_id)
        form_entity = {
            "id": form_id,
            "kind": "formulation",
            "canonical_name": example_id,
            "composition_status": "partial",
            "role": "patent_example",
        }
        result.entities[form_id] = form_entity

        if formulation.substrate and formulation.substrate.name.strip():
            substrate = resolve_substrate_surface(formulation.substrate.name)
            result.entities[substrate.entity_id] = resolved_to_upsert_fields(substrate)
            link_conf = min(base_conf, substrate.confidence)
            if link_conf >= settings.kg_relation_min_confidence:
                meta = {}
                if formulation.substrate.pre_treatment:
                    meta["pre_treatment"] = formulation.substrate.pre_treatment
                result.links.append(
                    FormulationLink(
                        src_entity_id=form_id,
                        dst_entity_id=substrate.entity_id,
                        link_type=StructuralLinkType.TESTED_ON_SUBSTRATE,
                        confidence=link_conf,
                        metadata=meta,
                        evidence_sentence=f"{example_id} 基材 {formulation.substrate.name}",
                    )
                )
            else:
                result.warnings.append(f"跳过低置信度基材边: {formulation.substrate.name}")

        for ingredient in formulation.ingredients:
            name = (ingredient.name or "").strip()
            if not name:
                continue
            resolved = resolve_material_surface(name, settings=settings)
            if resolved is None:
                result.warnings.append(f"无法解析成分: {name}")
                continue
            result.entities[resolved.entity_id] = resolved_to_upsert_fields(resolved)
            link_conf = min(base_conf, resolved.confidence)
            if link_conf < settings.kg_relation_min_confidence:
                result.warnings.append(f"跳过低置信度成分边: {name}")
                continue
            meta: dict = {"ingredient_name": name}
            if ingredient.concentration is not None:
                meta["concentration"] = ingredient.concentration
            if ingredient.unit:
                meta["unit"] = ingredient.unit
            if ingredient.role:
                meta["role"] = ingredient.role
            result.links.append(
                FormulationLink(
                    src_entity_id=form_id,
                    dst_entity_id=resolved.entity_id,
                    link_type=StructuralLinkType.HAS_INGREDIENT,
                    confidence=link_conf,
                    metadata=meta,
                    evidence_sentence=f"{example_id} 含 {name}",
                )
            )

        for perf in formulation.performances:
            metric_name = (perf.metric or "").strip()
            if not metric_name:
                continue
            metric = resolve_metric_surface(metric_name)
            result.entities[metric.entity_id] = resolved_to_upsert_fields(metric)
            link_conf = min(base_conf, metric.confidence)
            if link_conf < settings.kg_relation_min_confidence:
                result.warnings.append(f"跳过低置信度性能边: {metric_name}")
                continue
            meta = {"metric": metric_name}
            if perf.value is not None:
                meta["value"] = perf.value
            if perf.unit:
                meta["unit"] = perf.unit
            if perf.conditions:
                meta["conditions"] = perf.conditions
            sentence = f"{example_id} {metric_name}"
            if perf.value is not None:
                sentence += f" {perf.value}{perf.unit or ''}"
            result.links.append(
                FormulationLink(
                    src_entity_id=form_id,
                    dst_entity_id=metric.entity_id,
                    link_type=StructuralLinkType.HAS_PERFORMANCE,
                    confidence=link_conf,
                    metadata=meta,
                    evidence_sentence=sentence,
                )
            )

    return result
