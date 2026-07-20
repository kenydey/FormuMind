"""Soft clarification for ambiguous chat queries."""
from __future__ import annotations

import logging
import re

from ..config import Settings, get_settings
from ..domain.chat_schemas import ChatTurn, ClarificationOption, ClarifiedEntity, StructuredAnswer
from .errors import degrade_return

logger = logging.getLogger(__name__)

_AMBIGUOUS_TERMS: dict[str, list[tuple[str, str | None]]] = {
    "水性": [
        ("waterborne acrylic emulsion（水乳液）", None),
        ("water-reducible solvent paint（水稀释溶剂型）", None),
    ],
    "快干": [
        ("常温自干体系", None),
        ("低温烘烤加速体系", None),
    ],
    "环氧": [
        ("双酚A型环氧树脂", "chem:catalog:bisphenol_a_epoxy"),
        ("环氧改性丙烯酸", None),
    ],
}

_PRONOUN_ONLY = re.compile(r"^(那|它|这个|怎么样|如何)[？?]?$")


def detect_clarification(
    question: str,
    history: list[ChatTurn] | None,
    clarified_entities: list[ClarifiedEntity] | None,
    *,
    settings: Settings | None = None,
) -> ClarificationOption | None:
    settings = settings or get_settings()
    if not settings.chat_clarification_enabled:
        return None

    q = (question or "").strip()
    if not q or _PRONOUN_ONLY.match(q):
        return None

    resolved_terms = {(ce.term or "").strip() for ce in (clarified_entities or []) if ce.term}

    try:
        kg_option = _clarify_via_kg(q, resolved_terms, settings)
        if kg_option:
            return kg_option
        return _clarify_via_lexicon(q, resolved_terms)
    except Exception as exc:
        degrade_return(logger, exc, "chat clarification failed", None)
        return None


def apply_assumption_to_structured(
    structured: StructuredAnswer | None,
    clarification: ClarificationOption | None,
) -> StructuredAnswer | None:
    if structured is None or clarification is None:
        return structured
    assumption = (
        f"「{clarification.ambiguous_term}」按 {clarification.possible_meanings[0]} 解释"
        if clarification.possible_meanings
        else f"「{clarification.ambiguous_term}」存在多种含义"
    )
    assumptions = list(structured.assumptions or [])
    if assumption not in assumptions:
        assumptions.append(assumption)
    return structured.model_copy(update={"assumptions": assumptions})


def _clarify_via_kg(
    question: str,
    resolved_terms: set[str],
    settings: Settings,
) -> ClarificationOption | None:
    if not settings.kg_enabled:
        return None
    from .kg.entity_resolver import resolve_query

    resolved = resolve_query(question, settings=settings)
    if len(resolved.chemicals) >= 2 and len(resolved.trade_products) >= 1:
        term = question[:32]
        if term in resolved_terms:
            return None
        meanings = [c.canonical_name for c in resolved.chemicals[:3]]
        meanings += [t.trade_name for t in resolved.trade_products[:2]]
        ids = [c.id for c in resolved.chemicals[:3]] + [t.id for t in resolved.trade_products[:2]]
        return ClarificationOption(
            ambiguous_term=term,
            possible_meanings=meanings,
            question=f"你提到的实体可能指：{' / '.join(meanings[:3])}？",
            candidate_entity_ids=ids[:5],
        )
    return None


def _clarify_via_lexicon(question: str, resolved_terms: set[str]) -> ClarificationOption | None:
    for term, candidates in _AMBIGUOUS_TERMS.items():
        if term not in question or term in resolved_terms:
            continue
        meanings = [c[0] for c in candidates]
        ids = [c[1] for c in candidates if c[1]]
        return ClarificationOption(
            ambiguous_term=term,
            possible_meanings=meanings,
            question=f"你说的「{term}」更接近哪一种？",
            candidate_entity_ids=[i for i in ids if i],
        )
    return None
