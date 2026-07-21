"""Rule-based semantic relation extraction from linked chunk mentions (KG-R1)."""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from ...config import Settings, get_settings
from ...db.models import KGEntity, KGMention
from ...domain.kg_schemas import RelationType, SEMANTIC_RELATION_TYPES

logger = logging.getLogger(__name__)

_NEGATION_RE = re.compile(r"并非|不是|无协同|没有替代|未替代|cannot replace|not a substitute", re.IGNORECASE)

# (relation_type, pattern, src_group, dst_group)
_RULE_PATTERNS: list[tuple[RelationType, re.Pattern[str], int, int]] = [
    (
        RelationType.SUBSTITUTES,
        re.compile(r"(.+?)替代(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.SUBSTITUTES,
        re.compile(r"(.+?)代替(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.SUBSTITUTES,
        re.compile(r"(.+?)\s+replaces\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.SUBSTITUTES,
        re.compile(r"instead of\s+(.+?),?\s+use\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        2,
        1,
    ),
    (
        RelationType.SYNERGIZES,
        re.compile(r"(.+?)与(.+?)协同", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.SYNERGIZES,
        re.compile(r"(.+?)和(.+?)配合", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.SYNERGIZES,
        re.compile(r"(.+?)\s+synergiz\w*\s+with\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.INHIBITS,
        re.compile(r"(.+?)抑制(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.INHIBITS,
        re.compile(r"(.+?)\s+inhibits?\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.CORRELATES_POS,
        re.compile(r"(.+?)增加[，,](.+?)提高", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.CORRELATES_POS,
        re.compile(r"positive correlation between\s+(.+?)\s+and\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.CORRELATES_NEG,
        re.compile(r"(.+?)增加[，,](.+?)下降", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.CORRELATES_NEG,
        re.compile(r"(.+?)升高[，,](.+?)降低", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.REQUIRES,
        re.compile(r"(.+?)需要(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.REQUIRES,
        re.compile(r"(.+?)\s+requires?\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
    (
        RelationType.REQUIRES,
        re.compile(r"(.+?)\s+depends on\s+(.+?)(?:[，。；;,\.]|\Z)", re.IGNORECASE),
        1,
        2,
    ),
]


@dataclass(frozen=True)
class ExtractedRelation:
    source_entity_id: str
    target_entity_id: str
    relation_type: RelationType
    sentence: str
    confidence: float
    extraction_method: str = "rule"


def _resolve_entity_id(
    fragment: str,
    mentions: list[KGMention],
    entities: dict[str, KGEntity],
) -> str | None:
    text = (fragment or "").strip().lower()
    if len(text) < 2:
        return None
    best_id: str | None = None
    best_len = 0
    for mention in mentions:
        surface = (mention.surface_form or "").strip().lower()
        if len(surface) < 2:
            continue
        if surface in text or text in surface:
            if len(surface) > best_len:
                best_id = mention.entity_id
                best_len = len(surface)
    for eid, ent in entities.items():
        for candidate in (
            ent.canonical_name,
            ent.zh_name,
            ent.cas_no or "",
            ent.linked_catalog_key or "",
        ):
            cand = (candidate or "").strip().lower()
            if len(cand) < 3:
                continue
            if cand in text or text in cand:
                if len(cand) > best_len:
                    best_id = eid
                    best_len = len(cand)
    return best_id


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；;!\?])\s*|\n+", text or "")
    return [p.strip() for p in parts if p.strip()]


_LLM_RELATION_TYPES = {t.value for t in RelationType}


def _entity_labels(mentions: list[KGMention], entities: dict[str, KGEntity]) -> list[dict[str, str]]:
    labels: list[dict[str, str]] = []
    seen: set[str] = set()
    for mention in mentions:
        eid = mention.entity_id
        if eid in seen:
            continue
        seen.add(eid)
        ent = entities.get(eid)
        surfaces = [mention.surface_form] if mention.surface_form else []
        if ent:
            for candidate in (ent.zh_name, ent.canonical_name, ent.cas_no or ""):
                if candidate and candidate not in surfaces:
                    surfaces.append(candidate)
        labels.append(
            {
                "entity_id": eid,
                "labels": ", ".join(s for s in surfaces if s)[:200],
            }
        )
    return labels


def _build_llm_prompt(chunk_text: str, entity_labels: list[dict[str, str]]) -> str:
    entities_json = "\n".join(
        f'- id={item["entity_id"]} labels="{item["labels"]}"' for item in entity_labels
    )
    types = ", ".join(sorted(_LLM_RELATION_TYPES))
    text = (chunk_text or "")[:3000]
    return f"""Extract chemical/material semantic relations from the passage below.

Known entities in this chunk (use entity_id values exactly):
{entities_json}

Allowed relation_type values: {types}

Return JSON:
{{"relations": [
  {{"source_entity_id": "...", "target_entity_id": "...", "relation_type": "substitutes",
    "sentence": "verbatim supporting phrase", "confidence": 0.0-1.0}}
]}}

Rules:
- Only relations explicitly supported by the text.
- source_entity_id/target_entity_id must be from the entity list.
- Skip negated statements (not a substitute, cannot replace).
- Prefer fewer high-confidence relations over guesses.

Passage:
{text}
"""


def _extract_relations_llm(
    chunk_text: str,
    mentions: list[KGMention],
    entities: dict[str, KGEntity],
) -> list[ExtractedRelation]:
    from .. import llm

    labels = _entity_labels(mentions, entities)
    if len(labels) < 2:
        return []
    data = llm.complete_json(_build_llm_prompt(chunk_text, labels))
    if not data:
        return []
    raw_items = data.get("relations") or []
    if not isinstance(raw_items, list):
        return []

    valid_ids = {item["entity_id"] for item in labels}
    found: list[ExtractedRelation] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        src_id = str(item.get("source_entity_id") or "").strip()
        dst_id = str(item.get("target_entity_id") or "").strip()
        rel_raw = str(item.get("relation_type") or "").strip()
        if src_id not in valid_ids or dst_id not in valid_ids or src_id == dst_id:
            continue
        if rel_raw not in _LLM_RELATION_TYPES:
            continue
        sentence = str(item.get("sentence") or "")[:500]
        if sentence and _NEGATION_RE.search(sentence):
            continue
        try:
            confidence = float(item.get("confidence", 0.58))
        except (TypeError, ValueError):
            confidence = 0.58
        confidence = max(0.0, min(1.0, confidence))
        found.append(
            ExtractedRelation(
                source_entity_id=src_id,
                target_entity_id=dst_id,
                relation_type=RelationType(rel_raw),
                sentence=sentence or chunk_text[:200],
                confidence=confidence,
                extraction_method="llm",
            )
        )
    return found


def extract_relations_from_chunk(
    chunk_text: str,
    mentions: list[KGMention],
    entities: dict[str, KGEntity],
    *,
    source_id: str,
    chunk_id: str,
    settings: Settings | None = None,
) -> list[ExtractedRelation]:
    """Extract semantic relations using rules and optional LLM fallback."""
    settings = settings or get_settings()
    if not settings.kg_relation_extract_enabled:
        return []
    if len(mentions) < 2:
        return []

    found: list[ExtractedRelation] = []
    for sentence in _split_sentences(chunk_text):
        if _NEGATION_RE.search(sentence):
            continue
        for rel_type, pattern, src_g, dst_g in _RULE_PATTERNS:
            for match in pattern.finditer(sentence):
                src_frag = match.group(src_g)
                dst_frag = match.group(dst_g)
                src_id = _resolve_entity_id(src_frag, mentions, entities)
                dst_id = _resolve_entity_id(dst_frag, mentions, entities)
                if not src_id or not dst_id or src_id == dst_id:
                    continue
                found.append(
                    ExtractedRelation(
                        source_entity_id=src_id,
                        target_entity_id=dst_id,
                        relation_type=rel_type,
                        sentence=match.group(0).strip()[:500],
                        confidence=0.62,
                        extraction_method="rule",
                    )
                )

    deduped = _dedupe_relations(found)
    if settings.kg_llm_relation_extract:
        llm_rels = _extract_relations_llm(chunk_text, mentions, entities)
        if llm_rels:
            deduped = _dedupe_relations(deduped + llm_rels)
    return deduped


def _dedupe_relations(relations: list[ExtractedRelation]) -> list[ExtractedRelation]:
    grouped: dict[tuple[str, str, str], ExtractedRelation] = {}
    for rel in relations:
        key = (rel.source_entity_id, rel.target_entity_id, rel.relation_type.value)
        if key not in grouped:
            grouped[key] = rel
        elif rel.confidence > grouped[key].confidence:
            grouped[key] = rel
    return list(grouped.values())


def relation_type_values() -> frozenset[str]:
    return SEMANTIC_RELATION_TYPES
