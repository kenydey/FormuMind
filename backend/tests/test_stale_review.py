"""Tests for W5-3 (P1-29): stale review detection.

Covers: artifact content drift → stale=True; unchanged artifact → stale=False;
corrupt run file → "unverified"; qa-scope drift → stale=True; missing scope
inputs → "unverified"; legacy run without scope digest → "unverified";
missing artifact version → "unverified"; run_fix_loop records scope.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import artifact_versions as av
from app.services import reviewer_fix_loop as rfl


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(av, "_data_root", lambda: tmp_path)
    return tmp_path


def _version_with_content(content: bytes) -> str:
    lineage = av.create_lineage(project_id="proj-1", name="report-a", kind="report")
    v = av.create_version(lineage.lineage_id, content)
    return v.version_id


def test_artifact_change_marks_stale(tmp_data):
    vid = _version_with_content(b"v1 content")
    run = rfl.new_review_run(scope={"artifact_version_id": vid})
    rfl.save_review_run(run)

    loaded = rfl.load_review_run(run["run_id"])
    assert loaded is not None
    assert loaded["stale"] is False
    assert loaded["stale_reason"] is None

    av.set_version_content(vid, b"v2 content -- changed")
    loaded = rfl.load_review_run(run["run_id"])
    assert loaded["stale"] is True
    assert "artifact content changed" in (loaded["stale_reason"] or "")


def test_artifact_unchanged_not_stale(tmp_data):
    vid = _version_with_content(b"stable content")
    run = rfl.new_review_run(scope={"artifact_version_id": vid})
    rfl.save_review_run(run)

    loaded = rfl.load_review_run(run["run_id"])
    assert loaded is not None
    assert loaded["stale"] is False
    assert loaded["stale_reason"] is None
    assert loaded["scope_kind"] == "artifact"
    assert loaded["artifact_version_id"] == vid


def test_corrupt_run_file_unverified(tmp_data):
    run = rfl.new_review_run(scope={"question": "q", "answer": "a"})
    path = rfl._run_path(run["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    loaded = rfl.load_review_run(run["run_id"])
    assert loaded is not None
    assert loaded["stale"] == "unverified"
    assert "unreadable or corrupt" in (loaded["stale_reason"] or "")


def test_missing_run_file_returns_none(tmp_data):
    assert rfl.load_review_run("does-not-exist") is None


def test_qa_scope_drift_marks_stale(tmp_data):
    run = rfl.new_review_run(
        project_id="p1", scope={"project_id": "p1", "question": "q", "answer": "a"}
    )
    rfl.save_review_run(run)
    same = {"question": "q", "answer": "a", "project_id": "p1"}

    loaded = rfl.load_review_run(run["run_id"], scope=dict(same))
    assert loaded["stale"] is False
    assert loaded["stale_reason"] is None

    drifted = dict(same, answer="a -- edited afterwards")
    loaded = rfl.load_review_run(run["run_id"], scope=drifted)
    assert loaded["stale"] is True
    assert "scope changed" in (loaded["stale_reason"] or "")


def test_qa_scope_without_inputs_unverified(tmp_data):
    run = rfl.new_review_run(scope={"question": "q", "answer": "a"})
    rfl.save_review_run(run)

    loaded = rfl.load_review_run(run["run_id"])  # no current scope provided
    assert loaded["stale"] == "unverified"
    assert "not provided" in (loaded["stale_reason"] or "")


def test_legacy_run_without_digest_unverified(tmp_data):
    run = rfl.new_review_run()  # no scope → no digest (legacy-style)
    assert run["scope_digest"] is None
    rfl.save_review_run(run)

    loaded = rfl.load_review_run(run["run_id"], scope={"question": "q", "answer": "a"})
    assert loaded["stale"] == "unverified"
    assert "legacy run" in (loaded["stale_reason"] or "")


def test_missing_artifact_version_unverified(tmp_data):
    run = rfl.new_review_run(scope={"artifact_version_id": "no-such-version"})
    # creation-time lookup failed → digest None, but linkage recorded
    assert run["scope_kind"] == "artifact"
    assert run["scope_digest"] is None
    rfl.save_review_run(run)

    loaded = rfl.load_review_run(run["run_id"])
    assert loaded["stale"] == "unverified"


def test_run_fix_loop_records_scope(tmp_data):
    class Settings:
        evidence_reviewer_fix_loop_enabled = True

    vid = _version_with_content(b"reviewed artifact")

    def fake_review(question, answer, citations, *, settings):
        return {"status": "failure", "notes": ["n"], "suggestion": "s"}

    import app.services.evidence_reviewer as er

    orig = er.review_answer
    er.review_answer = fake_review
    try:
        ans, meta = rfl.run_fix_loop(
            question="q",
            answer="a",
            citations=[],
            review={"status": "failure", "notes": ["n"]},
            settings=Settings(),
            repair_fn=lambda q, a: "a",  # unchanged → break immediately
            project_id="p1",
            artifact_version_id=vid,
        )
    finally:
        er.review_answer = orig

    assert ans == "a"
    assert meta is not None
    run = rfl.load_review_run(meta["run_id"])
    assert run is not None
    assert run["scope_kind"] == "artifact"
    assert run["artifact_version_id"] == vid
    assert run["scope_digest"]
    assert run["stale"] is False

    # artifact 变更后同一 run 标 stale
    av.set_version_content(vid, b"changed after review")
    run = rfl.load_review_run(meta["run_id"])
    assert run["stale"] is True


def test_new_run_defaults_stale_contract(tmp_data):
    run = rfl.new_review_run()
    assert run["stale"] is False
    assert run["stale_reason"] is None
    # contract keys always present
    assert "stale" in run and "stale_reason" in run
