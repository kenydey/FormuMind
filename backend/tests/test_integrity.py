"""Orphan detection over the references that carry no database constraint.

Several of these cannot become foreign keys — under the Datalab backend the
target row lives in an external ELN — so the alternative to a constraint that
cannot be enforced is a check that actually runs. Without it orphans accumulate
silently and the first symptom is a retrieval quietly returning less than it
should.
"""
from __future__ import annotations

import uuid

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.integrity import check_integrity, purge_orphans
from app.db.models import DocumentChunk, KGEntity, KGMention, SourceDocument
from app.db.session_utils import commit_session


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/integrity.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _source(session, source_id: str = "doc-1") -> str:
    session.add(
        SourceDocument(id=source_id, filename="f.pdf", content_hash=uuid.uuid4().hex)
    )
    return source_id


def _chunk(session, source_id: str, ord_: int = 0) -> str:
    chunk_id = str(uuid.uuid4())
    session.add(
        DocumentChunk(id=chunk_id, source_id=source_id, ord=ord_, text="body")
    )
    return chunk_id


def _ref(report, name: str):
    return next(r for r in report.references if r.reference.startswith(name))


# ── clean databases ──────────────────────────────────────────────────────────


def test_empty_database_is_healthy(factory):
    report = check_integrity(factory)
    assert report.total_orphans == 0
    assert report.healthy is False  # the unjoinable references are always reported
    assert all(r.orphans == 0 for r in report.references)


def test_well_formed_data_reports_no_orphans(factory):
    with commit_session(factory) as session:
        source_id = _source(session)
        _chunk(session, source_id)
    report = check_integrity(factory)
    assert _ref(report, "document_chunks.source_id").orphans == 0
    assert report.total_orphans == 0


# ── it finds real orphans ────────────────────────────────────────────────────


def test_detects_chunks_whose_source_is_gone(factory):
    with commit_session(factory) as session:
        source_id = _source(session)
        _chunk(session, source_id, 0)
        _chunk(session, "deleted-source", 1)

    check = _ref(check_integrity(factory), "document_chunks.source_id")
    assert check.orphans == 1
    assert check.checked == 2
    assert not check.healthy


def test_detects_mentions_pointing_at_a_missing_entity(factory):
    with commit_session(factory) as session:
        session.add(KGEntity(id="chem:a", kind="chemical", canonical_name="A"))
        session.add(
            KGMention(id="m1", entity_id="chem:a", source_id="s", chunk_id="c",
                      surface_form="A")
        )
        session.add(
            KGMention(id="m2", entity_id="chem:ghost", source_id="s", chunk_id="c",
                      surface_form="Ghost")
        )
    assert _ref(check_integrity(factory), "kb_mentions.entity_id").orphans == 1


def test_total_orphans_sums_only_joinable_references(factory):
    with commit_session(factory) as session:
        _chunk(session, "missing-a", 0)
        _chunk(session, "missing-b", 1)
    report = check_integrity(factory)
    assert report.total_orphans == 2
    # The unjoinable rows must not contribute a bogus count.
    assert all(r.orphans == 0 for r in report.references if r.unjoinable)


# ── things that are not orphans ──────────────────────────────────────────────


def test_doe_plan_references_are_joinable_after_the_type_fix(factory):
    """These were String(36) against integer primary keys and could never join.
    Migration 0014 corrected the types, so they are ordinary checked
    references now rather than a permanent warning."""
    report = check_integrity(factory)
    for name in ("doe_plans.experiment_id", "doe_plans.campaign_id"):
        check = _ref(report, name)
        assert not check.unjoinable, name
        assert check.orphans == 0


def test_datalab_backed_reference_is_not_treated_as_an_orphan(factory):
    """Sample refs point into an external ELN; a missing local row is expected."""
    check = _ref(check_integrity(factory), "campaigns.sample_refs")
    assert check.unjoinable
    assert check.orphans == 0


def test_null_references_are_skipped_not_counted(factory):
    """A nullable reference left empty is not an orphan."""
    with commit_session(factory) as session:
        session.add(
            KGMention(id="m3", entity_id="", source_id="s", chunk_id="c", surface_form="x")
        )
    assert _ref(check_integrity(factory), "kb_mentions.entity_id").checked == 0


# ── purge ────────────────────────────────────────────────────────────────────


def test_purge_removes_orphans_and_leaves_valid_rows(factory):
    with commit_session(factory) as session:
        source_id = _source(session)
        _chunk(session, source_id, 0)
        _chunk(session, "gone", 1)

    deleted = purge_orphans(factory)
    assert deleted["document_chunks"] == 1

    report = check_integrity(factory)
    assert _ref(report, "document_chunks.source_id").orphans == 0
    assert _ref(report, "document_chunks.source_id").checked == 1


def test_purge_is_idempotent(factory):
    with commit_session(factory) as session:
        _chunk(session, "gone", 0)
    assert purge_orphans(factory)["document_chunks"] == 1
    assert purge_orphans(factory)["document_chunks"] == 0


def test_purge_touches_only_derived_knowledge_base_rows(factory):
    """Chunks and mentions can be rebuilt by re-indexing. Experiment and
    campaign rows cannot, and may legitimately reference an external system —
    deleting one because a lookup failed would destroy the data this guards."""
    deleted = purge_orphans(factory)
    assert set(deleted) == {"document_chunks", "kb_mentions"}


# ── endpoint ─────────────────────────────────────────────────────────────────


def test_integrity_endpoint_reports_every_reference():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/api/kb/integrity")
    assert response.status_code == 200
    body = response.json()
    assert "healthy" in body and "total_orphans" in body
    names = {r["reference"] for r in body["references"]}
    assert any("document_chunks.source_id" in n for n in names)
    assert any("doe_plans.experiment_id" in n for n in names)
