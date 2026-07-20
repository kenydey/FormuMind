"""Claim-level sourcing for chat answers."""
from __future__ import annotations

import logging
import re

from ..config import Settings, get_settings
from ..domain.chat_schemas import SourcedClaim, StructuredAnswer
from ..domain.schemas import Evidence
from ..pipeline.claim_checker import ClaimVerdict, verify_claim_offline, verify_claims_llm
from .errors import degrade_return

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*")


def build_sourced_claims(
    question: str,
    answer: str,
    sources: list[Evidence],
    structured: StructuredAnswer | None = None,
    *,
    settings: Settings | None = None,
) -> list[SourcedClaim] | None:
    settings = settings or get_settings()
    if not settings.chat_claim_check_enabled:
        return None

    claims = _extract_claims(answer, structured)
    if not claims:
        return []

    try:
        verified = verify_claims_llm(question, claims, sources)
    except Exception:
        verified = [verify_claim_offline(c, sources) for c in claims]

    out: list[SourcedClaim] = []
    for v in verified:
        chunk_ids = _indices_to_chunk_ids(v.evidence_indices, sources)
        status = _map_verdict(v.verdict)
        conf = 0.9 if status == "supported" else 0.4 if status == "weak" else 0.1
        out.append(
            SourcedClaim(
                text=v.text,
                chunk_ids=chunk_ids,
                confidence=conf,
                status=status,
            )
        )
    return out


def _extract_claims(answer: str, structured: StructuredAnswer | None) -> list[str]:
    if structured and structured.key_findings:
        return [c.strip() for c in structured.key_findings if c.strip()]
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(answer or "") if p.strip()]
    return parts[:8]


def _indices_to_chunk_ids(indices: list[int], sources: list[Evidence]) -> list[str]:
    ids: list[str] = []
    for idx in indices:
        if 0 <= idx < len(sources):
            ident = sources[idx].identifier or ""
            if ident.startswith("kb:"):
                ids.append(ident[3:])
            elif ident:
                ids.append(ident)
    return list(dict.fromkeys(ids))


def _map_verdict(verdict: ClaimVerdict) -> str:
    if verdict == ClaimVerdict.supported:
        return "supported"
    if verdict in (ClaimVerdict.insufficient, ClaimVerdict.conflicting):
        return "weak"
    return "unsupported"
