"""Natural-language intent parsing endpoint (v0.6, P1)."""
from __future__ import annotations

from fastapi import APIRouter

from ..domain.schemas import IntentParseRequest, IntentResult
from ..services.intent import parse_intent

router = APIRouter(prefix="/api", tags=["intent"])


@router.post("/intent/parse", response_model=IntentResult)
def parse(req: IntentParseRequest) -> IntentResult:
    """Parse a free-text R&D brief into a structured Requirement.

    Uses the configured LLM when available; falls back to an offline
    regex/keyword heuristic so it works with no API key.
    """
    return parse_intent(req.text)
