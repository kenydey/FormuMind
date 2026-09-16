"""P0 KG self-evolution: push workbench measured results back into the KG.

Closed-loop optimization converges on formulations; their *measured* performance
is written back into the knowledge graph so downstream recommendations improve
over time (a flywheel). Measured evidence is tagged with
``extraction_method="measured"`` so it is distinguishable from literature evidence
and is accumulated (never overwrites) via ``evidence_refs``.

Granularity (2026-09-16 MVP):
* **Material → property** from row ``actual_params`` / ``planned_params`` (preferred)
* **Domain → property** retained as aggregate fallback for legacy readers

Real-environment adaptations (learned from live-DB validation):
* ``Campaign`` has no ``domain`` column, so we read it defensively and fall back to
  a candidate list (``anticorrosion_coating`` -> ``corrosion`` -> ``Corrosion``) to
  resolve the domain entity that actually exists in the KG.
* The KG may not yet contain property entities for every metric (e.g. the live DB
  only has ``Corrosion`` / ``Coatings`` trade products). When a metric cannot be
  resolved we *create* a ``property`` entity from the campaign's objective
  ``display_name`` so measured evidence still lands and the KG self-enriches.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy.orm import attributes

from ..config import get_settings
from ..db.entity_store import get_entity_store, SEMANTIC_LINK_TYPES
from ..db.campaign_store import get_campaign_store
from ..db.models import KGEntityLink
from ..db.session_utils import commit_session

logger = logging.getLogger(__name__)

_SKIP_PARAM_KEYS = frozenset(
    {
        "cure_temperature_c",
        "temperature_c",
        "bake_c",
        "time_min",
        "cure_time_min",
        "ph",
        "run_id",
        "id",
    }
)


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_").lower()
    return (s[:48] or "material")


def _resolve_entity_id(store, name: str) -> str | None:
    """Best-effort entity resolution by display name; None if not in KG."""
    if not name:
        return None
    hits = store.search_entities(name, limit=1)
    return hits[0].id if hits else None


def _resolve_domain_id(store, campaign) -> str | None:
    """Resolve the campaign's domain entity, tolerant of missing ``domain`` attr
    and of KG naming (e.g. ``Corrosion`` rather than ``anticorrosion_coating``)."""
    candidates: list[str] = []
    dom = getattr(campaign, "domain", "") or ""
    if dom:
        candidates.append(str(dom))
    candidates += ["anticorrosion_coating", "anticorrosion", "corrosion", "Corrosion"]
    for cand in candidates:
        eid = _resolve_entity_id(store, cand)
        if eid:
            return eid
    return None


def _resolve_or_create_property(store, session, metric: str, display_name: str | None) -> str | None:
    """Resolve a performance-property entity; create it if absent so measured
    evidence can always land (KG self-enrichment)."""
    eid = _resolve_entity_id(store, metric)
    if eid:
        return eid
    label = (display_name or metric).strip()
    eid = _resolve_entity_id(store, label)
    if eid:
        return eid
    try:
        store.upsert_entity(
            session,
            id=f"prop:{metric}",
            canonical_name=label or metric,
            kind="property",
        )
        return f"prop:{metric}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("kg_feedback: could not create property entity %s: %s", metric, exc)
        return None


def _resolve_or_create_material(store, session, name: str) -> str | None:
    """Resolve a chemical/material entity; create ``mat:{slug}`` if absent."""
    label = (name or "").strip()
    if not label or label.lower() in _SKIP_PARAM_KEYS:
        return None
    eid = _resolve_entity_id(store, label)
    if eid:
        return eid
    mid = f"mat:{_slug(label)}"
    try:
        store.upsert_entity(
            session,
            id=mid,
            canonical_name=label,
            kind="chemical",
        )
        return mid
    except Exception as exc:  # pragma: no cover
        logger.warning("kg_feedback: could not create material entity %s: %s", label, exc)
        return None


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


def _objective_display_names(campaign) -> dict[str, str]:
    """Map metric id -> human display name from the campaign objectives snapshot."""
    snap = getattr(campaign, "objectives_snapshot", None) or []
    out: dict[str, str] = {}
    for obj in snap:
        if isinstance(obj, dict) and obj.get("metric"):
            out[obj["metric"]] = obj.get("display_name") or obj["metric"]
    return out


def _collect_measurements(rows) -> dict[str, float]:
    measured: dict[str, float] = {}
    for row in rows:
        for metric, val in (row.measurements or {}).items():
            if isinstance(val, (int, float)):
                measured[metric] = float(val)
    return measured


def _collect_material_names(rows) -> list[str]:
    """Unique factor names from completed / measured rows (actual over planned)."""
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        params = row.actual_params or row.planned_params or {}
        if not isinstance(params, dict):
            continue
        for key, val in params.items():
            if key in _SKIP_PARAM_KEYS:
                continue
            if not isinstance(val, (int, float)):
                continue
            label = str(key).strip()
            if not label or label.lower() in seen:
                continue
            seen.add(label.lower())
            names.append(label)
    return names


def _upsert_measured_link(
    session,
    *,
    src_id: str,
    dst_id: str,
    value: float,
    evidence_ref: dict[str, Any],
) -> bool:
    if src_id == dst_id or "measured_performance" not in SEMANTIC_LINK_TYPES:
        return False
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
        existing.evidence_refs = _merge_evidence_refs(existing.evidence_refs, evidence_ref)
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
    return True


def ingest_measured_evidence(campaign_id: int) -> int:
    """Write measured performance from a campaign's synced rows back to the KG.

    Returns the number of links written (material + domain). Safe no-ops when
    disabled or when neither domain nor materials can be resolved.
    """
    settings = get_settings()
    if not settings.kg_measured_feedback_enabled:
        return 0

    store = get_campaign_store()
    campaign = store.get_campaign_sync(campaign_id)
    if campaign is None:
        logger.warning("kg_feedback: campaign %s not found, skip", campaign_id)
        return 0

    rows = store.list_rows_sync(campaign_id)
    if not rows:
        return 0

    measured = _collect_measurements(rows)
    if not measured:
        return 0

    materials = _collect_material_names(rows)
    es = get_entity_store()
    domain_id = _resolve_domain_id(es, campaign)
    project_id = getattr(campaign, "project_id", None) or None
    display = _objective_display_names(campaign)

    if domain_id is None and not materials:
        logger.warning(
            "kg_feedback: no domain or materials resolvable for campaign %s, skip",
            campaign_id,
        )
        return 0

    written = 0
    with commit_session(es._session_factory) as session:
        material_ids: list[tuple[str, str]] = []
        for name in materials:
            mid = _resolve_or_create_material(es, session, name)
            if mid:
                material_ids.append((name, mid))

        for metric, value in measured.items():
            dst_id = _resolve_or_create_property(es, session, metric, display.get(metric))
            if dst_id is None:
                continue
            metric_label = display.get(metric, metric)

            for mat_name, mid in material_ids:
                evidence_ref: dict[str, Any] = {
                    "source_id": f"measured:campaign_{campaign_id}",
                    "extraction_method": "measured",
                    "sentence": f"实测 {mat_name}: {metric_label}={value}",
                    "granularity": "material",
                }
                if project_id:
                    evidence_ref["project_id"] = str(project_id)
                if _upsert_measured_link(
                    session, src_id=mid, dst_id=dst_id, value=value, evidence_ref=evidence_ref
                ):
                    written += 1

            if domain_id is not None:
                evidence_ref = {
                    "source_id": f"measured:campaign_{campaign_id}",
                    "extraction_method": "measured",
                    "sentence": f"实测 {metric_label}={value}",
                    "granularity": "domain",
                }
                if project_id:
                    evidence_ref["project_id"] = str(project_id)
                if _upsert_measured_link(
                    session,
                    src_id=domain_id,
                    dst_id=dst_id,
                    value=value,
                    evidence_ref=evidence_ref,
                ):
                    written += 1

    if written:
        logger.info(
            "kg_feedback: campaign %s wrote %d measured_performance links "
            "(materials=%d metrics=%d)",
            campaign_id,
            written,
            len(materials),
            len(measured),
        )
    return written
