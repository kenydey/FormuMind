"""Wave D — sources_audit grade mapping + locators."""
from __future__ import annotations

from app.domain.schemas import Evidence
from app.pipeline.claim_checker import ClaimVerdict, VerifiedClaim
from app.services.chat_claims import build_sources_audit


def _ev(ident: str, *, page: int | None = None, paragraph: int | None = None) -> Evidence:
    return Evidence(
        source="kb",
        identifier=ident,
        title=ident,
        snippet="epoxy wet adhesion 20%",
        relevance=0.9,
        page=page,
        paragraph=paragraph,
    )


def test_sources_audit_disabled():
    assert build_sources_audit([], verified=[], enabled=False) is None


def test_sources_audit_grades_and_locators():
    sources = [
        _ev("kb:c1", page=4, paragraph=2),
        _ev("kb:c2"),
    ]
    verified = [
        VerifiedClaim(
            text="wet adhesion improves 20%",
            verdict=ClaimVerdict.supported,
            evidence_indices=[0],
            reason="match",
        ),
        VerifiedClaim(
            text="VOC drops to zero",
            verdict=ClaimVerdict.conflicting,
            evidence_indices=[1],
            reason="source says otherwise",
        ),
        VerifiedClaim(
            text="cure at 3 C",
            verdict=ClaimVerdict.insufficient,
            evidence_indices=[],
        ),
        VerifiedClaim(
            text="unknown additive",
            verdict=ClaimVerdict.unsupported,
            evidence_indices=[],
        ),
    ]
    audit = build_sources_audit(sources, verified=verified, enabled=True)
    assert audit is not None
    assert audit["summary"]["supported"] == 1
    assert audit["summary"]["contradicted"] == 1
    assert audit["summary"]["partial"] == 1
    assert audit["summary"]["unsupported"] == 1
    row0 = audit["rows"][0]
    assert row0["grade"] == "supported"
    assert row0["locators"][0]["page"] == 4
    assert row0["locators"][0]["paragraph"] == 2
    row1 = audit["rows"][1]
    assert row1["grade"] == "contradicted"
    assert row1["locators"][0]["page"] is None
