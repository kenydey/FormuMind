"""Evidence provenance aggregation."""
from __future__ import annotations

from app.services.scholar_helpers import build_evidence_provenance


def test_provenance_disabled():
    assert build_evidence_provenance(doi_results=[], enabled=False) is None


def test_provenance_supported():
    class C:
        status = "supported"

    p = build_evidence_provenance(
        doi_results=[{"doi": "10.1/x", "status": "ok", "notice_kind": "none"}],
        sourced_claims=[C()],
        enabled=True,
    )
    assert p["evidence_availability"] == "supported"


def test_provenance_unavailable():
    class C:
        status = "unsupported"

    p = build_evidence_provenance(
        doi_results=[],
        sourced_claims=[C(), C()],
        enabled=True,
    )
    assert p["evidence_availability"] == "unavailable"
