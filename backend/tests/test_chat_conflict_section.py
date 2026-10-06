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
    gated, _, abstained, _notices = _apply_answer_gates(
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
    gated, _, _, _notices = _apply_answer_gates("q", answer, [_ev()], [], verified, s)
    assert gated == answer


def test_conflict_section_disabled_by_flag():
    s = get_settings()
    s.chat_conflict_section_enabled = False
    verified = [
        VerifiedClaim(text="pH 应控制在 4", verdict=ClaimVerdict.conflicting,
                      evidence_indices=[0], reason="x"),
    ]
    answer = "pH 应控制在 4[^1]。"
    gated, _, _, _notices = _apply_answer_gates("q", answer, [_ev()], [], verified, s)
    assert "证据冲突" not in gated


def _wiki_ev():
    return Evidence(source="wiki", identifier="wiki:page1", title="wiki",
                    snippet="编译页", relevance=0.9)


def test_conflict_section_maps_filtered_to_full_indices():
    """P0-3：verified.evidence_indices 是过滤后下标，渲染必须映射回完整序号。

    citations=[wiki, rawA, rawB]，过滤后 verified 指向 [0,1]（即 rawA/rawB），
    冲突段应渲染 [^2], [^3]，而非 [^1], [^2]。
    """
    s = get_settings()
    citations = [_wiki_ev(), _ev("rawA"), _ev("rawB")]
    verified = [
        VerifiedClaim(text="pH 应控制在 4", verdict=ClaimVerdict.conflicting,
                      evidence_indices=[0, 1], reason="x"),
    ]
    answer = "pH 应控制在 4[^2][^3]。"
    gated, _, abstained, _ = _apply_answer_gates(
        "q", answer, citations, [], verified, s)
    assert abstained is False
    section = gated.split("【证据冲突】")[1]
    assert "[^2]" in section and "[^3]" in section, f"引用错位: {section}"
    assert "[^1]" not in section, f"不应指向 wiki: {section}"


def test_rebind_citations_to_answer():
    """P0-4：repair 增删引用后，citations 按新答案实际 [^n] 重绑。"""
    from app.services.reviewer_fix_loop import rebind_citations_to_answer

    cits = [_ev("A"), _ev("B"), _ev("C")]
    # 新答案只引用了第 1、3 条（删了第 2 条）
    answer = "结论一[^1]，结论三[^3]。"
    new_answer, new_cits = rebind_citations_to_answer(answer, cits)
    assert [c.title for c in new_cits] == ["A", "C"]
    assert new_answer == "结论一[^1]，结论三[^2]。", new_answer


def test_rebind_keeps_hallucinated_marker():
    """P0-4：超出范围的 [^n]（幻觉引用）保留在文本中，供 reviewer 标记。"""
    from app.services.reviewer_fix_loop import rebind_citations_to_answer

    cits = [_ev("A")]
    answer = "结论[^1]，幻觉[^99]。"
    new_answer, new_cits = rebind_citations_to_answer(answer, cits)
    assert len(new_cits) == 1
    assert "[^99]" in new_answer, new_answer
    assert "[^1]" in new_answer
