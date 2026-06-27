"""Resolve objectives from Campaign snapshot for BayBE / workbench closed-loop."""
from __future__ import annotations

from ...domain.objective_contract import normalize_objectives, objectives_from_snapshot
from ...domain.schemas import ObjectiveSpec, Requirement
from ...db.campaign_store import CampaignStoreInterface, get_campaign_store


def resolve_campaign_objectives(
    store: CampaignStoreInterface | None,
    workbench_campaign_id: int | None,
    req: Requirement,
) -> list[ObjectiveSpec]:
    """Campaign.objectives_snapshot takes precedence when workbench is linked."""
    if store is not None and workbench_campaign_id is not None:
        campaign = store.get_campaign_sync(workbench_campaign_id)
        if campaign is not None and campaign.objectives_snapshot:
            return objectives_from_snapshot(campaign.objectives_snapshot, req.domain)
    return normalize_objectives(req)
