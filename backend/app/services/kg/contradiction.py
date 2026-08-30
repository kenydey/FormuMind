"""KG v10 — literature↔measured contradiction detection.

Read-only detector: compares a chemical/trade entity's *literature* semantic
relations (substitutes / synergizes / inhibits / correlates_*) against its
*measured* performance edges written by ``kg_feedback.ingest_measured_evidence``.

When a literature claim points one way but the team's own measured data points
the other, we emit a :class:`KGContradictionMark` so the UI can warn the formulator
and ``discover_substitutes`` can demote the candidate. We never mutate existing
edges — this is a pure read-side view over ``kb_entity_links``.

Direction-conflict table (the heart of the detector):

| literature edge | expected measured signal                         | contradiction if measured shows |
|-----------------|--------------------------------------------------|---------------------------------|
| substitutes      | substitute performs >= original (good)          | substitute clearly worse        |
| synergizes       | associated property improves                      | property clearly worse          |
| inhibits         | associated property worsens                       | property clearly better         |
| correlates_pos   | property moves with the linked entity            | property moves opposite         |
| correlates_neg   | property moves opposite the linked entity        | property moves with it          |
| requires         | linked entity present improves outcome           | outcome poor despite presence   |

Strength = normalized measured deviation × literature confidence.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from ...config import get_settings
from ...db.entity_store import get_entity_store
from ...domain.kg_schemas import (
    KGContradictionMark,
    KGContradictionResponse,
    RelationType,
)
from ..kg.entity_resolver import resolve_query

logger = logging.getLogger(__name__)

# Literature edges that have a directional expectation against measured data.
_LITERATURE_TYPES = [
    "substitutes",
    "synergizes",
    "inhibits",
    "correlates_pos",
    "correlates_neg",
    "requires",
]

# Map a literature relation to (contradiction_type, expected_sign).
# expected_sign: +1 means measured should be high/good, -1 means low/bad.
_EXPECTED_SIGN = {
    "substitutes": ("substitute_vs_poor", +1),
    "synergizes": ("synergy_vs_poor", +1),
    "inhibits": ("inhibit_vs_good", -1),
    "correlates_pos": ("correlate_vs_opposite", +1),
    "correlates_neg": ("correlate_vs_with", -1),
    "requires": ("require_vs_poor", +1),
}


def _entity_display_name(entity_id: str, cache: dict[str, str]) -> str:
    if entity_id in cache:
        return cache[entity_id]
    row = get_entity_store().get_entity(entity_id)
    name = (row.canonical_name if row else entity_id) or entity_id
    if row and getattr(row, "zh_name", None):
        name = f"{row.zh_name} ({name})"
    cache[entity_id] = name
    return name


def detect_contradictions(entity_id: str) -> KGContradictionResponse:
    """Return literature↔measured contradictions for ``entity_id``.

    Entities without measured edges return an empty ``contradictions`` list
    (no error). Entities without literature edges likewise return empty.
    """
    settings = get_settings()
    threshold = float(getattr(settings, "kg_contradiction_threshold", 0.3))
    store = get_entity_store()
    cache: dict[str, str] = {}

    lit_links = store.get_links_for_entity(
        entity_id,
        direction="both",
        link_types=_LITERATURE_TYPES,
        limit=200,
    )
    measured_links = store.get_links_for_entity(
        entity_id,
        direction="both",
        link_types=["measured_performance"],
        limit=200,
    )

    marks: list[KGContradictionMark] = []
    if not measured_links:
        return KGContradictionResponse(
            entity_id=entity_id,
            entity_name=_entity_display_name(entity_id, cache),
            contradictions=marks,
        )

    # Index measured performance anchored at this entity (domain). measured
    # edges connect (domain) -> (property); the normalized value encodes how
    # well the domain performs on that property. We key by property id but the
    # *anchor* is always entity_id, so a poor domain measurement contradicts
    # every positively-framed literature claim about that domain.
    measured_values: list[tuple[float, str]] = []
    for ml in measured_links:
        if getattr(ml, "extraction_method", None) != "measured":
            continue
        other = ml.dst_entity_id if ml.src_entity_id == entity_id else ml.src_entity_id
        val = float(getattr(ml, "confidence", 0.5))
        src = ""
        refs = getattr(ml, "evidence_refs", None) or []
        if refs:
            src = refs[0].get("source_id", "") if isinstance(refs[0], dict) else ""
        measured_values.append((val, src))

    if not measured_values:
        return KGContradictionResponse(
            entity_id=entity_id,
            entity_name=_entity_display_name(entity_id, cache),
            contradictions=marks,
        )

    # Domain-level measured signal: average normalized performance across props.
    domain_perf = sum(v for v, _ in measured_values) / len(measured_values)

    for link in lit_links:
        lit_type = link.link_type
        if lit_type not in _EXPECTED_SIGN:
            continue
        if getattr(link, "extraction_method", "rule") == "measured":
            continue  # only compare literature claims vs measured
        ctype, expected_sign = _EXPECTED_SIGN[lit_type]
        target = link.dst_entity_id if link.src_entity_id == entity_id else link.src_entity_id
        lit_conf = float(getattr(link, "confidence", 0.5))
        # expected_sign +1 => claim expects good performance; contradiction when
        # domain_perf low. -1 => expects poor; contradiction when domain_perf high.
        if expected_sign > 0:
            deviation = (0.5 - domain_perf) * 2  # +1 when perf low
        else:
            deviation = (domain_perf - 0.5) * 2  # +1 when perf high
        strength = abs(deviation) * lit_conf
        if strength < threshold:
            continue
        marks.append(
            KGContradictionMark(
                target_entity_id=target,
                target_entity_name=_entity_display_name(target, cache),
                literature_relation=RelationType(lit_type),
                literature_confidence=lit_conf,
                measured_property=target,
                measured_value=domain_perf,
                measured_source_id=measured_values[0][1],
                contradiction_type=ctype,
                strength=round(strength, 3),
                recommended_action=(
                    "review_experiment"
                    if expected_sign > 0
                    else "demote_literature_edge"
                ),
            )
        )

    marks.sort(key=lambda m: -m.strength)
    return KGContradictionResponse(
        entity_id=entity_id,
        entity_name=_entity_display_name(entity_id, cache),
        contradictions=marks,
    )


def detect_contradictions_by_query(q: str) -> KGContradictionResponse:
    """Resolve a free-text query to an entity, then detect contradictions."""
    resolved = resolve_query(q)
    entity_id: str | None = None
    if resolved.chemicals:
        entity_id = resolved.chemicals[0].id
    elif resolved.trade_products:
        entity_id = resolved.trade_products[0].id
    if not entity_id:
        return KGContradictionResponse(entity_id="", contradictions=[])
    return detect_contradictions(entity_id)
