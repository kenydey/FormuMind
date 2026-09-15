"""P3 supplier normalization — sanitize, dual-write, backfill."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, make_engine, make_session_factory
from app.db.material_store import MaterialStore
from app.db.models import MaterialSupplierRow, SupplierRow
from app.services.supplier_normalize import (
    MAX_SUPPLIERS_PER_MATERIAL,
    sanitize_supplier_records,
)


@pytest.fixture()
def store(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/suppliers.db")
    Base.metadata.create_all(engine)
    return MaterialStore(make_session_factory(engine))


def test_sanitize_whitelists_aliases_dedupes_and_caps():
    raw = [
        {"SourceName": "Acme Chem", "SourceURL": "https://acme.example", "junk": 1},
        {"name": "Acme Chem", "url": "https://acme.example"},  # dup by norm+url
        {"name": "Acme Chem", "product_url": "https://acme.example/p/1"},  # kept: different product
        {"name": ""},
        {"vendor": "Beta Co"},
    ] + [{"name": f"Vendor {i}"} for i in range(MAX_SUPPLIERS_PER_MATERIAL)]
    cleaned = sanitize_supplier_records(raw)
    assert cleaned[0]["name"] == "Acme Chem"
    assert cleaned[0]["url"] == "https://acme.example"
    assert "junk" not in cleaned[0]
    names = [c["name"] for c in cleaned]
    assert names.count("Acme Chem") == 2  # homepage vs product page
    assert "Beta Co" in names
    assert len(cleaned) == MAX_SUPPLIERS_PER_MATERIAL


def test_upsert_dual_writes_normalized_tables(store):
    ok = store.upsert(
        "Zinc phosphate",
        {
            "role": "pigment",
            "suppliers_json": [
                {"SourceName": "PubChem Vendor A", "SourceURL": "https://a.example"},
                {"name": "PubChem Vendor A", "url": "https://a.example"},  # dup
                {"name": "Vendor B", "product_url": "https://b.example/item"},
            ],
        },
        origin="user",
        overwrite=True,
    )
    assert ok is True
    row = store.get("Zinc phosphate")
    assert row is not None
    assert len(row.suppliers_json) == 2
    assert {s["name"] for s in row.suppliers_json} == {"PubChem Vendor A", "Vendor B"}

    with store._session_factory() as session:
        assert session.query(SupplierRow).count() == 2
        assert session.query(MaterialSupplierRow).count() == 2


def test_backfill_from_legacy_json(store):
    # Simulate legacy row: JSON present, no links yet (write JSON via raw column).
    store.upsert("Epoxy resin", {"role": "resin"}, origin="seed", overwrite=True)
    with store._session_factory() as session:
        from app.db.material_store import norm_key
        from app.db.models import MaterialRow

        row = session.query(MaterialRow).filter(MaterialRow.norm_key == norm_key("Epoxy resin")).one()
        row.suppliers_json = [
            {"name": "Legacy Vendor", "url": "https://legacy.example"},
            {"name": "Legacy Vendor", "url": "https://legacy.example"},
        ]
        session.commit()

    stats = store.backfill_suppliers(clear_json=False)
    assert stats["with_json"] >= 1
    assert stats["links_written"] >= 1
    row = store.get("Epoxy resin")
    assert row.suppliers_json[0]["name"] == "Legacy Vendor"
    with store._session_factory() as session:
        assert session.query(SupplierRow).filter(SupplierRow.norm_name == "legacy vendor").count() == 1


def test_backfill_clear_json_still_hydrates(store):
    store.upsert(
        "Talc",
        {"role": "filler", "suppliers_json": [{"name": "Mineral Co"}]},
        overwrite=True,
    )
    stats = store.backfill_suppliers(clear_json=True)
    assert stats["json_cleared"] >= 1
    # Projection cleared on disk, but get() hydrates from link tables.
    with store._session_factory() as session:
        from app.db.material_store import norm_key
        from app.db.models import MaterialRow

        raw = session.query(MaterialRow).filter(MaterialRow.norm_key == norm_key("Talc")).one()
        assert raw.suppliers_json is None
    row = store.get("Talc")
    assert row.suppliers_json == [{"name": "Mineral Co", "url": None, "product_url": None}]
