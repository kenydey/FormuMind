"""Unit tests for publication_preflight steel-stamp."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import publication_preflight as pf


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pf, "_data_root", lambda: tmp_path)
    return tmp_path


def test_citation_unbound_blocking(tmp_data):
    md = "Claim with [^1] and [^99].\n\n[^1]: Source A\n"
    findings = pf.run_checks(md, total_anchors=1)
    blocking = [f for f in findings if f.check == "citation" and f.severity == "blocking"]
    assert blocking
    assert any("99" in f.title or "99" in f.detail for f in blocking)


def test_placeholder_blocking(tmp_data):
    md = "Some text [citation needed] and TODO later.\n"
    findings = pf.run_checks(md)
    kinds = {f.check for f in findings}
    assert "placeholder" in kinds
    assert all(f.severity == "blocking" for f in findings if f.check == "placeholder")


def test_numeric_major_without_citation(tmp_data):
    md = "The coating cured at 120 °C for 2 h with 5 wt% additive.\n"
    findings = pf.run_checks(md)
    numeric = [f for f in findings if f.check == "numeric"]
    assert numeric
    assert all(f.severity == "major" for f in numeric)


def test_numeric_ok_with_nearby_citation(tmp_data):
    md = "Cured at 120 °C [^1] with good adhesion.\n\n[^1]: Paper\n"
    findings = pf.run_checks(md, total_anchors=1)
    numeric = [f for f in findings if f.check == "numeric"]
    assert not numeric


def test_review_override_finalize(tmp_data):
    md = "Text with [citation needed].\n"
    state = pf.review_markdown("proj-a", "storm", md)
    assert state["open_blocking"] >= 1
    fid = state["findings"][0]["id"]
    with pytest.raises(ValueError):
        pf.override_finding("proj-a", "storm", fid, actor="", reason="")
    state2 = pf.override_finding(
        "proj-a", "storm", fid, actor="alice", reason="placeholder accepted for draft"
    )
    assert all(
        f["status"] == "overridden"
        for f in state2["findings"]
        if f["id"] == fid
    )
    # Still may have other open findings — override all blocking
    for f in list(state2["findings"]):
        if f["severity"] == "blocking" and f["status"] == "open":
            pf.override_finding(
                "proj-a", "storm", f["id"], actor="alice", reason="ok"
            )
    ready = pf.assert_ready("proj-a", "storm", md, actor="alice")
    assert ready["ready"] is True
    assert ready["state"]["finalization"]


def test_export_allowed_blocks_then_allows(tmp_data):
    class S:
        publication_preflight_enabled = True

    md = "TODO fix this claim about 10 wt%.\n"
    ok, detail = pf.export_allowed("proj-b", "storm", md, settings=S())
    assert ok is False
    assert detail.get("ready") is False

    # Override all blocking
    state = pf.get_state("proj-b", "storm")
    for f in state["findings"]:
        if f["severity"] == "blocking" and f["status"] == "open":
            pf.override_finding(
                "proj-b", "storm", f["id"], actor="bob", reason="accepted"
            )
    ok2, detail2 = pf.export_allowed("proj-b", "storm", md, settings=S())
    assert ok2 is True
    assert detail2.get("ok") is True


def test_export_skipped_when_flag_off(tmp_data):
    class S:
        publication_preflight_enabled = False

    ok, detail = pf.export_allowed(
        "proj-c", "storm", "TODO still here", settings=S()
    )
    assert ok is True
    assert detail.get("skipped") is True


def test_hash_change_blocks_finalize(tmp_data):
    md1 = "Clean text with [^1].\n\n[^1]: src\n"
    pf.review_markdown("proj-d", "storm", md1, total_anchors=1)
    # Override nothing needed if clean — may have numeric? none.
    result = pf.assert_ready("proj-d", "storm", md1 + "\nextra", actor="x")
    assert result["ready"] is False
    assert any("hash" in e.lower() for e in result["errors"])
