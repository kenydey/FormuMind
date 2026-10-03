"""Two silent failures found by tracing swallowed exceptions (round-3 audit).

* ``kb_ingest_audit._try_sqlite`` imported ``session_scope`` — a name
  ``db/session.py`` never had. The ImportError was swallowed, so the SQL table
  documented as the primary store never received a row; everything fell through
  to the JSONL file.
* The SSRF guard used ``is_private`` & co. but ``100.64.0.0/10`` (carrier-grade
  NAT, e.g. Alibaba's metadata endpoint 100.100.100.200) is neither "private"
  nor "reserved" to the stdlib, so it was fetchable.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import KbIngestAudit
from app.services import ingestion
from app.services import kb_ingest_audit as audit
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.db.database import default_session_factory

    db_url = f"sqlite:///{tmp_path}/audit.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUMIND_DB_URL", db_url)
    get_settings.cache_clear()
    yield default_session_factory()
    get_settings.cache_clear()


# ── audit rows reach SQL ────────────────────────────────────────────────────


def test_ingest_audit_row_is_written_to_sql_not_the_jsonl_fallback(db, monkeypatch):
    def jsonl_must_not_be_used(row):
        raise AssertionError("fell back to JSONL: the SQL path is broken again")

    monkeypatch.setattr(audit, "_append_jsonl", jsonl_must_not_be_used)

    row = audit.record_ingest_audit(
        project_id="p1",
        domain="anticorrosion_coating",
        query="salt spray zinc phosphate",
        evidence_id="US123",
        source="patents",
        action="accept",
        reason="topic_match",
        domain_match="exact",
    )

    with db() as s:
        stored = s.execute(select(KbIngestAudit)).scalars().all()
    assert [r.id for r in stored] == [row["id"]]
    only = stored[0]
    assert (only.project_id, only.action, only.reason, only.evidence_id) == (
        "p1", "accept", "topic_match", "US123",
    )
    assert only.query_fingerprint == row["query_fingerprint"]


def test_overlong_reason_is_clipped_to_the_column(db, monkeypatch):
    monkeypatch.setattr(audit, "_append_jsonl", lambda row: (_ for _ in ()).throw(AssertionError))
    audit.record_ingest_audit(
        project_id=None, domain=None, query=None, evidence_id=None,
        source="x", action="skip", reason="r" * 200,
    )
    with db() as s:
        assert len(s.execute(select(KbIngestAudit.reason)).scalar_one()) == 64


def test_sql_failure_still_falls_back_to_jsonl(monkeypatch, tmp_path):
    """The contract the module advertises: SQL first, JSONL when SQL is unavailable."""
    import app.db.session as session_mod

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(session_mod, "get_db_session", boom)
    seen: list[dict] = []
    monkeypatch.setattr(audit, "_append_jsonl", seen.append)

    row = audit.record_ingest_audit(
        project_id="p", domain="d", query="q", evidence_id="e", source="s",
        action="accept", reason="ok",
    )
    assert seen == [row]


# ── SSRF guard ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    [
        "100.64.0.1",          # CGNAT, low end
        "100.100.100.200",     # Alibaba Cloud metadata service
        "100.127.255.254",     # CGNAT, high end
        "169.254.169.254",     # AWS/GCP/Azure metadata
        "127.0.0.1",
        "10.1.2.3",
        "192.168.0.10",
        "172.16.5.5",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:127.0.0.1",    # IPv4-mapped loopback
        "::ffff:100.100.100.200",
        "0.0.0.0",
    ],
)
def test_internal_addresses_are_blocked(addr):
    assert ingestion._is_blocked_ip(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_stay_allowed(addr):
    assert ingestion._is_blocked_ip(ipaddress.ip_address(addr)) is False


def test_literal_cgnat_url_is_rejected():
    assert ingestion._is_safe_url("http://100.100.100.200/latest/meta-data/") is False
    assert ingestion._is_safe_url("http://100.64.0.1:8080/") is False
