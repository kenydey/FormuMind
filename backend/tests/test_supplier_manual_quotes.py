"""A′: manual supplier commercial fields + stale_price annotation."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.material_store import MaterialStore
from app.db.models import MaterialSupplierRow, SupplierRow
from app.services.supplier_normalize import (
    is_stale_price,
    sanitize_supplier_records,
)


@pytest.fixture()
def store(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/suppliers_a.db")
    Base.metadata.create_all(engine)
    # Soft columns for existing-table path
    from app.db.database import _ensure_supplier_quote_columns

    _ensure_supplier_quote_columns(engine)
    return MaterialStore(make_session_factory(engine))


def test_sanitize_manual_quote_fields_and_stamps_observed():
    cleaned = sanitize_supplier_records(
        [
            {
                "name": "Acme",
                "country": "CN",
                "price_cny_per_kg": "12.5",
                "moq": "25kg",
                "pack_size": "25kg bag",
                "lead_time_days": "14",
            }
        ]
    )
    assert len(cleaned) == 1
    row = cleaned[0]
    assert row["country"] == "CN"
    assert row["price_cny_per_kg"] == 12.5
    assert row["price_source"] == "manual"
    assert row["currency"] == "CNY"
    assert row["moq"] == "25kg"
    assert row["lead_time_days"] == 14
    assert row["price_observed_at"]  # stamped on manual price
    assert row["stale_price"] is False


def test_stale_price_when_observed_old():
    old = datetime.utcnow() - timedelta(days=200)
    assert is_stale_price(price_cny_per_kg=10.0, price_observed_at=old) is True
    assert is_stale_price(price_cny_per_kg=10.0, price_observed_at=None) is True
    assert is_stale_price(price_cny_per_kg=None, price_observed_at=None) is False


def test_upsert_persists_manual_quote_fields(store):
    ok = store.upsert(
        "Zinc phosphate",
        {
            "role": "pigment",
            "suppliers_json": [
                {
                    "name": "Vendor A",
                    "url": "https://a.example",
                    "country": "CN",
                    "price_cny_per_kg": 18.0,
                    "price_source": "manual",
                    "price_observed_at": "2026-09-01",
                    "moq": "200kg",
                    "pack_size": "25kg",
                    "lead_time_days": 10,
                }
            ],
        },
        origin="user",
        overwrite=True,
    )
    assert ok is True
    row = store.get("Zinc phosphate")
    assert row is not None
    assert len(row.suppliers_json) == 1
    s = row.suppliers_json[0]
    assert s["country"] == "CN"
    assert float(s["price_cny_per_kg"]) == 18.0
    assert s["price_source"] == "manual"
    assert s["moq"] == "200kg"
    assert s["lead_time_days"] == 10
    assert "stale_price" in s

    with store._session_factory() as session:
        link = session.query(MaterialSupplierRow).one()
        assert link.price_cny_per_kg == 18.0
        assert link.lead_time_days == 10
        supplier = session.query(SupplierRow).one()
        assert supplier.country == "CN"
