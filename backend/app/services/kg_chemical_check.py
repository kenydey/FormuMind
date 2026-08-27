"""KG-driven chemical feasibility check for DOE candidate formulations.

This is the *deterministic, zero-LLM-cost* chemical constraint source for the
closed-loop DOE generator and the recommendation ranker. For each candidate
formulation it resolves the material names to KG entities (best-effort, by
canonical-name match) and asks the knowledge graph whether any pair of
materials shares an ``INHIBITS`` (incompatible) relation. A hit marks the
candidate ``infeasible`` with a human-readable reason sourced from the
relation's evidence sentence.

Optionally (for the recommendation ranker) it also surfaces ``SYNERGIZES``
relations as a positive signal.

Design notes
------------
* No LLM calls. ``feasibility.check_formulation`` exists but routes through a
  blocking agent review per candidate — far too expensive to run inside the
  DOE loop. KG relation lookup is a deterministic DB read.
* Soft constraint by design: a material that cannot be resolved to a KG entity
  is simply skipped (we constrain only what the graph actually knows), so a
  sparse KG degrades to "no chemical constraint" rather than blocking the loop.
* KG off (``kg_enabled is False``) → returns feasible, no work done.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import get_settings
from ..domain.schemas import Formulation

logger = logging.getLogger(__name__)

# Relation types that express material incompatibility / hostility.
_INCOMPATIBLE_RELATIONS = ("inhibits",)

# Minimum confidence for a relation to count as a hard incompatibility.
_MIN_RELATION_CONFIDENCE = 0.55

# Minimum normalised-name match score proxy: we require the search to return a
# reasonably confident entity. search_entities orders by mention_count; we cap
# how loose the name match may be by only accepting top-1 when its canonical
# name shares a meaningful token overlap with the query material name.
_NAME_OVERLAP_MIN = 1


@dataclass
class ChemicalCheckResult:
    feasible: bool
    status: str = "pass"  # pass | warn | infeasible
    reasons: list[str] = field(default_factory=list)
    incompatible_pairs: list[tuple[str, str, str]] = field(default_factory=list)
    # Positive signal for the recommendation ranker (only populated when
    # include_synergies=True): material pairs sharing a SYNERGIZES relation.
    synergy_pairs: list[tuple[str, str, str]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.feasible


def _resolve_entity_id(material_name: str) -> str | None:
    """Best-effort resolve a free-text material name to a KG entity id."""
    from ..db.entity_store import get_entity_store

    store = get_entity_store()
    hits = store.search_entities(material_name, limit=5)
    if not hits:
        return None
    # Prefer the most-mentioned hit whose canonical name shares at least one
    # meaningful token with the query, to avoid false links (e.g. "resin" →
    # some unrelated "resin X").
    q_tokens = {t for t in material_name.lower().split() if len(t) > 1}
    for h in hits:
        cname = (h.canonical_name or "").lower()
        c_tokens = {t for t in cname.split() if len(t) > 1}
        if q_tokens & c_tokens:
            return h.id
    # Fall back to top hit only if names are very close in length (loose guard).
    if abs(len(hits[0].canonical_name) - len(material_name)) <= 3:
        return hits[0].id
    return None


def _incompatible_pairs_for(entity_id: str) -> list[tuple[str, str, str]]:
    """Return [(other_entity_id, relation_type, evidence_sentence), ...]."""
    from ..services.kg.graph_query import get_entity_relations

    rels = get_entity_relations(
        entity_id,
        direction="both",
        link_types=list(_INCOMPATIBLE_RELATIONS),
        limit=50,
    )
    out = []
    for r in rels:
        if r.confidence < _MIN_RELATION_CONFIDENCE:
            continue
        other = r.source_entity_id if r.target_entity_id == entity_id else r.target_entity_id
        sentence = ""
        if r.evidence:
            sentence = r.evidence[0].sentence
        out.append((other, r.relation_type.value, sentence))
    return out


# Relation types that express material synergy / beneficial combination.
_SYNERGY_RELATIONS = ("synergizes",)


def _synergy_pairs_for(entity_id: str) -> list[tuple[str, str, str]]:
    """Return [(other_entity_id, relation_type, evidence_sentence), ...]."""
    from ..services.kg.graph_query import get_entity_relations

    rels = get_entity_relations(
        entity_id,
        direction="both",
        link_types=list(_SYNERGY_RELATIONS),
        limit=50,
    )
    out = []
    for r in rels:
        if r.confidence < _MIN_RELATION_CONFIDENCE:
            continue
        other = r.source_entity_id if r.target_entity_id == entity_id else r.target_entity_id
        sentence = ""
        if r.evidence:
            sentence = r.evidence[0].sentence
        out.append((other, r.relation_type.value, sentence))
    return out


def check_formulation_chemistry(
    form: Formulation,
    *,
    include_synergies: bool = False,
) -> ChemicalCheckResult:
    """Deterministic KG chemical-compatibility check for a candidate formula.

    Returns ``feasible=True`` (status ``pass``) when KG is disabled, no
    material resolves, or no incompatibility is found. Marks ``infeasible``
    only on a concrete KG ``INHIBITS`` relation between two resolved materials.

    When ``include_synergies=True`` (recommendation ranker path) it also
    collects ``SYNERGIZES`` pairs as a positive signal.
    """
    settings = get_settings()
    if not settings.kg_enabled:
        return ChemicalCheckResult(feasible=True, status="pass")

    materials = [i.name for i in form.ingredients if i.name]
    if len(materials) < 2:
        return ChemicalCheckResult(feasible=True, status="pass")

    # Resolve each material to a KG entity id (best-effort).
    resolved: dict[str, str] = {}  # material_name -> entity_id
    for m in materials:
        eid = _resolve_entity_id(m)
        if eid:
            resolved[m] = eid

    if len(resolved) < 2:
        # Cannot evaluate pair-wise incompatibility without two resolved nodes.
        return ChemicalCheckResult(feasible=True, status="pass")

    reasons: list[str] = []
    pairs: list[tuple[str, str, str]] = []
    synergies: list[tuple[str, str, str]] = []
    name_by_eid: dict[str, str] = {eid: name for name, eid in resolved.items()}

    names = list(resolved.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            m_a, m_b = names[i], names[j]
            eid_a, eid_b = resolved[m_a], resolved[m_b]
            for other, rel_type, sentence in _incompatible_pairs_for(eid_a):
                if other == eid_b:
                    cause = sentence or f"知识图谱记录 {m_a} 与 {m_b} 存在不相容关系（{rel_type}）"
                    reasons.append(f"{m_a} 与 {m_b} 不相容：{cause}")
                    pairs.append((m_a, m_b, rel_type))
            if include_synergies:
                for other, rel_type, sentence in _synergy_pairs_for(eid_a):
                    if other == eid_b:
                        synergies.append((m_a, m_b, rel_type))

    if pairs:
        return ChemicalCheckResult(
            feasible=False,
            status="infeasible",
            reasons=reasons,
            incompatible_pairs=pairs,
            synergy_pairs=synergies,
        )
    return ChemicalCheckResult(
        feasible=True,
        status="pass",
        synergy_pairs=synergies,
    )
