"""Tests for post-generation Claim Checker."""
from __future__ import annotations

from app.config import Settings
from app.domain.schemas import Evidence
from app.pipeline.claim_checker import (
    ClaimVerdict,
    append_verification_footer,
    check_claims,
    extract_claims,
    verify_claim_offline,
)


def _evidence(snippet: str, source: str = "USPTO") -> Evidence:
    return Evidence(
        source=source,
        identifier="id1",
        title="Zinc phosphate epoxy primer",
        snippet=snippet,
        relevance=0.9,
    )


def test_extract_claims_from_bullets():
    md = "# Report\n\n## 关键发现\n\n- Waterborne epoxy with zinc phosphate inhibitor improves corrosion resistance.\n- Short line.\n"
    claims = extract_claims(md)
    assert len(claims) == 1
    assert "zinc phosphate" in claims[0].lower()


def test_verify_claim_offline_supported():
    ev = [_evidence("Waterborne epoxy with zinc phosphate inhibitor for steel substrates.")]
    claim = "Waterborne epoxy with zinc phosphate inhibitor improves corrosion resistance on steel."
    result = verify_claim_offline(claim, ev)
    assert result.verdict == ClaimVerdict.supported
    assert result.evidence_indices == [0]


def test_verify_claim_offline_unsupported():
    ev = [_evidence("Basic degreaser formulation for aluminum cleaning.")]
    claim = "Polyurethane topcoat delivers 5000 hours neutral salt spray on magnesium alloy substrates."
    result = verify_claim_offline(claim, ev)
    assert result.verdict == ClaimVerdict.unsupported


def test_check_claims_offline_pass_rate():
    report = (
        "## 关键发现\n\n"
        "- Waterborne epoxy with zinc phosphate inhibitor improves corrosion resistance.\n"
        "- Random fabricated metric with no basis in literature at all whatsoever.\n"
    )
    evidence = [_evidence("Waterborne epoxy with zinc phosphate inhibitor for corrosion protection.")]
    result = check_claims("epoxy primer", report, evidence, Settings())
    assert result.engine == "offline"
    assert len(result.claims) == 2
    assert 0.0 < result.pass_rate <= 1.0


def test_append_verification_footer_adds_section():
    from app.pipeline.claim_checker import ClaimCheckResult, VerifiedClaim

    result = ClaimCheckResult(
        claims=[
            VerifiedClaim(text="unsupported claim text here long enough", verdict=ClaimVerdict.unsupported),
        ],
        pass_rate=0.0,
        needs_regenerate=True,
        claim_check_passed=False,
    )
    out = append_verification_footer("# Report\n\nbody", result)
    assert "论断核验" in out
    assert "unsupported" in out
