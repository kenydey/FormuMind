"""P0 KG self-evolution: push workbench measured results back into the KG.

Closed-loop optimization converges on formulations; their *measured* performance
is currently discarded after training the surrogate. This module writes that
evidence back into the knowledge graph so downstream recommendations improve
over time (a flywheel). Measured evidence is tagged with
``extraction_method="measured"`` so it is distinguishable from literature evidence
and is accumulated (never overwrites) via ``evidence_refs``.

Scope (v1, pragmatic): the unit of feedback is
``(campaign domain entity, performance metric entity, measured value)``.
Component-level feedback (lever role -> chemical entity) needs a lever->chemical
mapping that is out of scope for v1; domain-level feedback is enough to prove the
loop end-to-end without risking KG pollution.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import attributes

from ..config import get_settings
from ..db.entity_store import get_entity_store, SEMANTIC_LINK_TYPES
from ..db.campaign_store import get_campaign_store
from ..db.models import KGEntityLink

logger = logging.getLogger(__name__)


def _resolve_entity_id(store, name: str) -> str | None:
    """Best-effort entity resolution by display name; None if not in KG."""
    if not name:
        return None
    hits = store.search_entities(name, limit=1)
    return hits[0].id if hits else None


def _normalize_confidence(value: float) -> float:
    """Measured evidence gets a moderate, bounded confidence (literature may
    outrank or underrank it later via extraction_method filtering)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.6
    return max(0.4, min(0.9, 0.6 + (v / (abs(v) + 1_000_000.0)) * 0.3))


def _merge_evidence_refs(existing_refs, evidence_ref):
    refs = list(existing_refs or [])
    key = (
        evidence_ref.get("source_id"),
        evidence_ref.get("chunk_id"),
        evidence_ref.get("sentence"),
    )
    if not any(
        (r.get("source_id"), r.get("chunk_id"), r.get("sentence")) == key for r in refs
    ):
        refs.append(evidence_ref)
    return refs[:20]


def ingest_measured_evidence(campaign_id: int) -> int:
    """Write measured performance from a campaign's synced rows back to the KG.

    Returns the number of links written. Safe no-ops when disabled, when the
    campaign/domain entity is missing, or when a metric has no KG entity.
    Uses its own session so the write is committed deterministically (bypassing
    ``merge_semantic_link``'s nested-savepoint path, which does not reliably
    persist in this SQLite setup).
    """
    settings = get_settings()
    if not settings.kg_measured_feedback_enabled:
        return 0

    store = get_campaign_store()
    campaign = store.get_campaign_sync(campaign_id)
    if campaign is None:
        logger.warning("kg_feedback: campaign %s not found, skip", campaign_id)
        return 0

    domain_name = (campaign.domain or "").strip()
    rows = store.list_rows_sync(campaign_id)
    if not rows:
        return 0

    measured: dict[str, float] = {}
    for row in rows:
        for metric, val in (row.measurements or {}).items():
            if isinstance(val, (int, float)):
                measured[metric] = float(val)

    if not measured:
        return 0

    es = get_entity_store()
    src_id = _resolve_entity_id(es, domain_name)
    if src_id is None:
        logger.warning("kg_feedback: domain entity %r not in KG, skip", domain_name)
        return 0

    written = 0
    with es._session_factory() as session:
        for metric, value in measured.items():
            dst_id = _resolve_entity_id(es, metric)
            if dst_id is None or dst_id == src_id:
                continue
            if "measured_performance" not in SEMANTIC_LINK_TYPES:
                continue
            evidence_ref = {
                "source_id": f"measured:campaign_{campaign_id}",
                "extraction_method": "measured",
                "sentence": f"实测 {metric}={value}",
            }
            existing = (
                session.query(KGEntityLink)
                .filter(
                    KGEntityLink.src_entity_id == src_id,
                    KGEntityLink.dst_entity_id == dst_id,
                    KGEntityLink.link_type == "measured_performance",
                )
                .first()
            )
            if existing is not None:
                existing.evidence_refs = _merge_evidence_refs(
                    existing.evidence_refs, evidence_ref
                )
                attributes.flag_modified(existing, "evidence_refs")
                existing.confidence = max(
                    float(existing.confidence or 0),
                    _normalize_confidence(value),
                )
                existing.extraction_method = "measured"
                existing.is_valid = True
                existing.updated_at = _utcnow()
            else:
                session.add(
                    KGEntityLink(
                        id=str(uuid.uuid4()),
                        src_entity_id=src_id,
                        dst_entity_id=dst_id,
                        link_type="measured_performance",
                        confidence=_normalize_confidence(value),
                        evidence_refs=[evidence_ref],
                        extraction_method="measured",
                        is_valid=True,
                        created_at=_utcnow(),
                        updated_at=_utcnow(),
                    )
                )
            written += 1
        session.commit()

    if written:
        logger.info(
            "kg_feedback: campaign %s wrote %d measured_performance links",
            campaign_id,
            written,
        )
    return written


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
