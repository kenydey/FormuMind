"""P2-3: conflicting verdict surfaced in the answer + raw_verdict field."""
from app.api.chat import _apply_answer_gates
from app.config import get_settings
from app.domain.schemas import Evidence
from app.pipeline.claim_checker import ClaimVerdict, VerifiedClaim
from app.services.chat_claims import _map_verdict, build_sourced_claims


def _ev(title="t", snippet="s"):
    return Evidence(source="literature", identifier="e1", title=title,
                    snippet=snippet, relevance=0.9)


def test_map_verdict_keeps_backcompat():
    # SourcedClaim.status 保持 back-compat：conflicting 仍映射为 weak。
    assert _map_verdict(ClaimVerdict.conflicting) == "weak"
    assert _map_verdict(ClaimVerdict.supported) == "supported"


def test_raw_verdict_field_preserved():
    verified = [
        VerifiedClaim(text="pH 应控制在 4", verdict=ClaimVerdict.conflicting,
                      evidence_indices=[0], reason="x"),
    ]
    claims = build_sourced_claims("q", "a", [_ev()], verified=verified,
                                  settings=get_settings())
    assert claims[0].status == "weak"
    assert claims[0].raw_verdict == "conflicting"


def test_conflict_section_appended():
    s = get_settings()
    verified = [
        VerifiedClaim(text="pH 应控制在 4", verdict=ClaimVerdict.conflicting,
                      evidence_indices=[0], reason="x"),
        VerifiedClaim(text="槽液温度 50 度", verdict=ClaimVerdict.supported,
                      evidence_indices=[0], reason="x"),
    ]
    answer = "pH 应控制在 4[^1]，槽液温度 50 度[^1]。"
    gated, _, abstained = _apply_answer_gates(
        "q", answer, [_ev()], [], verified, s)
    assert abstained is False
    assert "【证据冲突】" in gated
    assert "pH 应控制在 4" in gated
    assert "[^1]" in gated.split("【证据冲突】")[1]
    assert gated.startswith(answer)


def test_no_conflict_no_section():
    s = get_settings()
    verified = [
        VerifiedClaim(text="槽液温度 50 度", verdict=ClaimVerdict.supported,
                      evidence_indices=[0], reason="x"),
    ]
    answer = "槽液温度 50 度[^1]。"
    gated, _, _ = _apply_answer_gates("q", answer, [_ev()], [], verified, s)
    assert gated == answer


def test_conflict_section_disabled_by_flag():
    s = get_settings()
    s.chat_conflict_section_enabled = False
    verified = [
        VerifiedClaim(text="pH 应控制在 4", verdict=ClaimVerdict.conflicting,
                      evidence_indices=[0], reason="x"),
    ]
    answer = "pH 应控制在 4[^1]。"
    gated, _, _ = _apply_answer_gates("q", answer, [_ev()], [], verified, s)
    assert "证据冲突" not in gated
