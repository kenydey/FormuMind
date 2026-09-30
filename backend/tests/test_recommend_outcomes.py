"""C-8: recommend adopt telemetry — upsert idempotency, stats, project isolation.

- ``record_adopt`` upserts per recommend_id (no duplicate rows)
- invalid adopt_signal rejected
- ``outcome_stats`` aggregates counts / rate / by_signal; ``validated`` stays
  null by contract (strong-success layer pending C-5)
- project_id scoping excludes other projects' rows
- stats fail open when the table is missing (migration not applied)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import recommend_outcome_store
from app.db.database import make_engine, make_session_factory
from app.db.models import Base, RecommendOutcomeRow
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/recommend_outcomes.db"
    run_upgrade(db_url, monkeypatch)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _row_count(session: Session) -> int:
    return len(session.execute(select(RecommendOutcomeRow)).scalars().all())


# ── record_adopt ─────────────────────────────────────────────────────────────


def test_adopt_upsert_is_idempotent(session: Session) -> None:
    r1 = recommend_outcome_store.record_adopt(
        session,
        recommend_id="rec-1",
        project_id="proj-a",
        adopt_signal="button",
        formula_snapshot={"name": "f1"},
    )
    assert r1.adopted is True
    assert r1.formula_hash  # fingerprint reserved for C-5 correlation
    r2 = recommend_outcome_store.record_adopt(
        session,
        recommend_id="rec-1",
        project_id="proj-a",
        adopt_signal="copied",
        formula_snapshot={"name": "f1-updated"},
    )
    assert _row_count(session) == 1
    assert r2.id == r1.id
    assert r2.adopt_signal == "copied"
    assert r2.formula_snapshot == {"name": "f1-updated"}


def test_invalid_signal_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="adopt_signal"):
        recommend_outcome_store.record_adopt(
            session, recommend_id="rec-x", adopt_signal="telepathy"
        )
    with pytest.raises(ValueError, match="adopt_signal"):
        recommend_outcome_store.validate_adopt_signal("")


# ── outcome_stats ────────────────────────────────────────────────────────────


def test_stats_aggregation(session: Session) -> None:
    recommend_outcome_store.record_adopt(session, recommend_id="r1", adopt_signal="button")
    recommend_outcome_store.record_adopt(session, recommend_id="r2", adopt_signal="button")
    recommend_outcome_store.record_adopt(session, recommend_id="r3", adopt_signal="copied")
    stats = recommend_outcome_store.outcome_stats(session)
    assert stats["available"] is True
    assert stats["total"] == 3
    assert stats["adopted"] == 3
    assert stats["adopt_rate"] == 1.0
    assert stats["by_signal"] == {"button": 2, "copied": 1}
    assert stats["adopted_7d"] == 3
    # Strong-success layer pending C-5: null by contract, never fabricated.
    assert stats["validated"] is None
    assert "C-5" in stats["validated_note"]


def test_stats_empty_table(session: Session) -> None:
    stats = recommend_outcome_store.outcome_stats(session)
    assert stats["total"] == 0
    assert stats["adopt_rate"] == 0.0
    assert stats["by_signal"] == {}
    assert stats["validated"] is None


def test_stats_project_isolation(session: Session) -> None:
    recommend_outcome_store.record_adopt(
        session, recommend_id="ra", project_id="proj-a", adopt_signal="button"
    )
    recommend_outcome_store.record_adopt(
        session, recommend_id="rb", project_id="proj-b", adopt_signal="button"
    )
    stats_a = recommend_outcome_store.outcome_stats(session, project_id="proj-a")
    assert stats_a["total"] == 1
    stats_b = recommend_outcome_store.outcome_stats(session, project_id="proj-b")
    assert stats_b["total"] == 1


def test_stats_fail_open_when_table_missing(tmp_path: Path) -> None:
    """Simulates migration 0038 not applied: stats must not 500."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path}/no_recommend_table.db"
    engine = create_engine(db_url)
    # Create every table EXCEPT recommend_outcomes.
    for table in Base.metadata.sorted_tables:
        if table.name != "recommend_outcomes":
            table.create(engine)
    s = sessionmaker(bind=engine)()
    try:
        stats = recommend_outcome_store.outcome_stats(s)
        assert stats["available"] is False
        assert stats["total"] == 0
        assert stats["validated"] is None
    finally:
        s.close()
        engine.dispose()


def test_recent_adopted_ordering(session: Session) -> None:
    for i in range(3):
        recommend_outcome_store.record_adopt(session, recommend_id=f"rr-{i}")
    recent = recommend_outcome_store.recent_adopted(session, limit=2)
    assert len(recent) == 2
    assert recent[0]["recommend_id"] == "rr-2"


# ── register_round (the denominator) ───────────────────────────────────────


def test_register_round_creates_unadopted_row(session: Session) -> None:
    row = recommend_outcome_store.register_round(
        session, recommend_id="round-1", project_id="proj-a"
    )
    assert row.adopted is False
    assert row.recommend_id == "round-1"
    assert row.project_id == "proj-a"
    stats = recommend_outcome_store.outcome_stats(session)
    assert stats["total"] == 1
    assert stats["adopted"] == 0
    assert stats["adopt_rate"] == 0.0


def test_register_round_is_idempotent(session: Session) -> None:
    r1 = recommend_outcome_store.register_round(session, recommend_id="round-dup")
    r2 = recommend_outcome_store.register_round(session, recommend_id="round-dup")
    assert r1.id == r2.id
    assert _row_count(session) == 1


def test_register_then_adopt_flips_to_adopted(session: Session) -> None:
    recommend_outcome_store.register_round(session, recommend_id="round-2")
    row = recommend_outcome_store.record_adopt(
        session,
        recommend_id="round-2",
        adopt_signal="button",
        formula_snapshot={"name": "f"},
    )
    assert row.adopted is True
    assert row.formula_hash  # fingerprint derived from the snapshot in-store
    assert _row_count(session) == 1
    stats = recommend_outcome_store.outcome_stats(session)
    assert stats["total"] == 1
    assert stats["adopted"] == 1
    assert stats["adopt_rate"] == 1.0


def test_adopt_without_registration_still_works(session: Session) -> None:
    """Adopt arriving before/without round registration must not be lost."""
    row = recommend_outcome_store.record_adopt(
        session, recommend_id="round-orphan", adopt_signal="copied"
    )
    assert row.adopted is True
    # Late registration must not clobber the adopt.
    late = recommend_outcome_store.register_round(session, recommend_id="round-orphan")
    assert late.adopted is True
    assert _row_count(session) == 1


def test_mixed_rounds_give_honest_rate(session: Session) -> None:
    recommend_outcome_store.register_round(session, recommend_id="m1")
    recommend_outcome_store.register_round(session, recommend_id="m2")
    recommend_outcome_store.record_adopt(session, recommend_id="m1")
    stats = recommend_outcome_store.outcome_stats(session)
    assert stats["total"] == 2
    assert stats["adopted"] == 1
    assert stats["adopt_rate"] == 0.5


# ── API layer ────────────────────────────────────────────────────────────────


def test_api_adopt_and_stats() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    rid = "rec-api-test-1"
    r = client.post(
        f"/api/formulations/recommend/{rid}/adopt",
        json={
            "adopt_signal": "button",
            "formula_index": 0,
            "formula_snapshot": {"name": "api-f"},
            "project_id": "proj-adopt-api",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["adopted"] is True

    # invalid signal → 422
    r = client.post(
        f"/api/formulations/recommend/{rid}/adopt",
        json={"adopt_signal": "telepathy"},
    )
    assert r.status_code == 422

    r = client.get("/api/ops/recommend-stats", params={"project_id": "proj-adopt-api"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert body["validated"] is None
