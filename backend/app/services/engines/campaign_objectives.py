"""Resolve objectives from Campaign snapshot for BayBE / workbench closed-loop."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ...domain.objective_contract import normalize_objectives, objectives_from_snapshot
from ...domain.schemas import ObjectiveSpec, Requirement


def resolve_campaign_objectives(
    db: Session | None,
    workbench_campaign_id: int | None,
    req: Requirement,
) -> list[ObjectiveSpec]:
    """Campaign.objectives_snapshot takes precedence when workbench is linked."""
    if db is not None and workbench_campaign_id is not None:
        from ...db.models import Campaign

        campaign = db.get(Campaign, workbench_campaign_id)
        if campaign is not None and campaign.objectives_snapshot:
            return objectives_from_snapshot(campaign.objectives_snapshot, req.domain)
    return normalize_objectives(req)
