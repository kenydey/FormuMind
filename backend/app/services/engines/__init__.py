"""Optional DOE / Bayesian optimization engine adapters (pyDOE, baybe)."""

from .doe_registry import build_doe_plan, pydoe_available, baybe_available, resolve_doe_engine
from .baybe_engine import BaybeCampaignEngine
from .status import engines_status

__all__ = [
    "BaybeCampaignEngine",
    "build_doe_plan",
    "pydoe_available",
    "baybe_available",
    "resolve_doe_engine",
    "engines_status",
]
