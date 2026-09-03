"""Task 3.1: document_chunks unique constraint (source_id, ord)."""

from __future__ import annotations

import pytest
import uuid as _uuid
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import DocumentChunk


@pytest.fixture()
def session():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s


def _chunk_row(session: Session, source_id: str, ord: int, text: str) -> DocumentChunk:
    return DocumentChunk(
        id=str(_uuid.uuid4()),
        source_id=source_id,
        ord=ord,
        text=text,
    )


def test_unique_constraint_rejects_duplicate_source_ord(session: Session) -> None:
    """INSERT with same (source_id, ord) → IntegrityError."""
    source_id = "src-1"
    session.add(_chunk_row(session, source_id, 0, "first"))
    session.commit()

    session.add(_chunk_row(session, source_id, 0, "second"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_unique_constraint_allows_different_ord(session: Session) -> None:
    """Same source_id, different ord → allowed."""
    session.add(_chunk_row(session, "src-2", 0, "A"))
    session.add(_chunk_row(session, "src-2", 1, "B"))
    session.commit()  # no error


def test_unique_constraint_allows_different_source(session: Session) -> None:
    """Different source_id, same ord → allowed."""
    session.add(_chunk_row(session, "src-3", 0, "C"))
    session.add(_chunk_row(session, "src-4", 0, "D"))
    session.commit()  # no error


# ── migration 0010 upgrade/downgrade/idempotent tests ────────────────────────

_ALEMBIC_INI = __import__("pathlib").Path(__file__).resolve().parent.parent / "app" / "db" / "alembic.ini"


def test_migration_0010_upgrade_downgrade_upgrade(tmp_path, monkeypatch):
    """0010 upgrade → downgrade → re-upgrade all succeed (bidirectional)."""
    from tests.alembic_helpers import make_config, run_downgrade, run_upgrade

    import app.db.database as db_mod

    db_url = f"sqlite:///{tmp_path}/kb.db"
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: db_mod.make_session_factory(db_mod.make_engine(db_url)))

    run_upgrade(db_url, monkeypatch)                      # upgrade full chain
    run_downgrade(db_url, monkeypatch, "0009")            # downgrade 0010
    run_upgrade(db_url, monkeypatch)                      # re-upgrade → succeeds


def test_migration_0010_idempotent_rerun(tmp_path, monkeypatch):
    """Running upgrade twice with 0010 already applied → no-op."""
    from tests.alembic_helpers import make_config, run_upgrade

    import app.db.database as db_mod

    db_url = f"sqlite:///{tmp_path}/kb.db"
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: db_mod.make_session_factory(db_mod.make_engine(db_url)))

    run_upgrade(db_url, monkeypatch)   # first upgrade
    run_upgrade(db_url, monkeypatch)   # second upgrade → passes (inspect guard skips)
