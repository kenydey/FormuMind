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


def extract_relations_from_chunk(
    chunk_text: str,
    mentions: list[KGMention],
    entities: dict[str, KGEntity],
    *,
    source_id: str,
    chunk_id: str,
    settings: Settings | None = None,
) -> list[ExtractedRelation]:
    """Extract semantic relations using rule patterns aligned to chunk mentions."""
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

    return _dedupe_relations(found)


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
