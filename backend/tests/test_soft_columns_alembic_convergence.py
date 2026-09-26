"""P2: every runtime soft-column has an Alembic owner (dual-track convergence)."""
from __future__ import annotations

from pathlib import Path

from app.db.database import SOFT_COLUMN_ALEMBIC_OWNERS, soft_column_inventory

VERSIONS = Path(__file__).resolve().parents[1] / "app" / "db" / "alembic" / "versions"


def test_soft_inventory_matches_owner_registry():
    inv = soft_column_inventory()
    assert set(inv) == set(SOFT_COLUMN_ALEMBIC_OWNERS)
    for table, cols in inv.items():
        owners = SOFT_COLUMN_ALEMBIC_OWNERS[table]
        assert set(owners) == cols, f"{table}: soft cols {cols} != owners {set(owners)}"


def test_alembic_owners_exist_on_disk():
    revision_files = {p.stem for p in VERSIONS.glob("*.py") if p.name != "__init__.py"}
    for table, owners in SOFT_COLUMN_ALEMBIC_OWNERS.items():
        for col, rev in owners.items():
            if rev == "model/create_all":
                continue
            # Owner may be a stem prefix (0029_source_documents_archived).
            assert any(
                stem == rev or stem.startswith(rev) for stem in revision_files
            ), f"{table}.{col} owner {rev!r} missing under {VERSIONS}"


def test_no_orphan_soft_columns_without_docs():
    """Guard: adding a soft column requires updating SOFT_COLUMN_ALEMBIC_OWNERS."""
    inv = soft_column_inventory()
    for table, cols in inv.items():
        for col in cols:
            assert col in SOFT_COLUMN_ALEMBIC_OWNERS[table]
