"""Workbench row types — decoupled from SQLAlchemy (Datalab is SSOT for row data)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkbenchRow:
    """One AG-Grid execution row backed by a Datalab Sample ``item_id``."""

    id: int
    campaign_id: int
    item_id: str
    status: str = "Pending"
    planned_params: dict[str, Any] = field(default_factory=dict)
    actual_params: dict[str, float] = field(default_factory=dict)
    measurements: dict[str, Any] = field(default_factory=dict)
