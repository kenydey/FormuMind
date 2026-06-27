"""Tests for the SQLAlchemy persistence layer (B5).

Uses in-memory SQLite so no files are created and tests are fully isolated.
"""
import json

import httpx

from app.db.database import make_engine, make_session_factory
from app.db.migrate import migrate_from_stores, migrate_inline_sql_to_datalab
from app.db.store import DatalabExperimentStore, JsonExperimentStore, SqlExperimentStore
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.services.training import ModelRegistry
from tests.test_experiment_store_datalab import MockDatalabState, _mock_handler


def _sql_store(engine=None):
    if engine is None:
        engine = make_engine("sqlite:///:memory:")
    return SqlExperimentStore(make_session_factory(engine))


def _record(zinc: float = 8.0, salt_spray: float = 840.0) -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={"Zinc phosphate": zinc, "Bisphenol-A epoxy (DGEBA)": 38.0, "Polyamide hardener": 14.0},
        cure_temperature_c=80.0,
        measured={"salt_spray_hours": salt_spray},
        source="test",
    )


# ── SqlExperimentStore ────────────────────────────────────────────────────────

def test_sql_store_add_and_all():
    store = _sql_store()
    assert store.all() == []
    store.add([_record(6.0, 680.0), _record(10.0, 1000.0)])
    rows = store.all()
    assert len(rows) == 2
    assert rows[0].domain == ProductDomain.anticorrosion_coating
    assert rows[0].measured["salt_spray_hours"] == 680.0


def test_sql_store_count():
    store = _sql_store()
    store.add([_record()] * 5)
    assert store.count() == 5


def test_sql_store_clear():
    store = _sql_store()
    store.add([_record()] * 3)
    store.clear()
    assert store.count() == 0
    assert store.all() == []


def test_sql_store_append_on_successive_add():
    store = _sql_store()
    store.add([_record(4.0, 520.0)])
    store.add([_record(12.0, 1160.0)])
    assert store.count() == 2


def test_sql_store_concurrent_writes(tmp_path):
    """Multiple threads writing simultaneously must not lose rows."""
    import threading

    # Use a file-based SQLite so all threads share the same database.
    engine = make_engine(f"sqlite:///{tmp_path}/concurrent.db")
    store = _sql_store(engine)
    errors: list[Exception] = []

    def _add():
        try:
            store.add([_record()])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_add) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent write raised: {errors}"
    assert store.count() == 10


# ── ModelRegistry with SqlStore ───────────────────────────────────────────────

def test_registry_uses_sql_store_via_store_kwarg():
    store = _sql_store()
    reg = ModelRegistry(store=store)
    assert reg.total_records == 0

    records = [_record(zinc=float(z), salt_spray=200.0 + 80.0 * z) for z in range(2, 14)]
    reg.add(records)
    assert reg.total_records == 12
    assert any(i.metric == "salt_spray_hours" for i in reg.info())


def test_registry_reset_with_persist_wipes_sql_store():
    store = _sql_store()
    reg = ModelRegistry(store=store)
    reg.add([_record()] * 8)
    reg.reset(persist=True)
    assert reg.total_records == 0
    # The underlying store is also empty.
    assert store.count() == 0


def test_registry_load_reloads_from_sql_store():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    store1 = SqlExperimentStore(factory)

    reg1 = ModelRegistry(store=store1)
    reg1.add([_record(zinc=float(z), salt_spray=200.0 + 80.0 * z) for z in range(2, 14)])
    assert reg1.total_records == 12

    # Second registry against the same engine re-loads from the DB.
    store2 = SqlExperimentStore(factory)
    reg2 = ModelRegistry(store=store2)
    assert reg2.total_records == 12
    assert any(i.metric == "salt_spray_hours" for i in reg2.info())


# ── Migration helper ──────────────────────────────────────────────────────────

def test_migrate_json_to_sql(tmp_path):
    json_path = tmp_path / "experiments.json"
    records_data = [
        {
            "domain": "anticorrosion_coating",
            "factors": {"Zinc phosphate": 8.0},
            "cure_temperature_c": 80.0,
            "measured": {"salt_spray_hours": 840.0},
            "source": "lab",
            "label": "",
        }
    ]
    json_path.write_text(json.dumps(records_data))

    engine = make_engine("sqlite:///:memory:")
    sql_store = SqlExperimentStore(make_session_factory(engine))

    migrated = migrate_from_stores(str(json_path), sql_store)
    assert migrated == 1
    assert sql_store.count() == 1
    records = sql_store.all()
    assert records[0].measured["salt_spray_hours"] == 840.0


def test_migrate_skips_when_db_not_empty(tmp_path):
    json_path = tmp_path / "experiments.json"
    json_path.write_text(json.dumps([{
        "domain": "anticorrosion_coating",
        "factors": {}, "cure_temperature_c": None,
        "measured": {"salt_spray_hours": 800.0}, "source": "lab", "label": "",
    }]))

    engine = make_engine("sqlite:///:memory:")
    sql_store = SqlExperimentStore(make_session_factory(engine))
    sql_store.add([_record()])  # pre-populate → migration must skip

    migrated = migrate_from_stores(str(json_path), sql_store)
    assert migrated == 0
    assert sql_store.count() == 1  # unchanged


def test_migrate_inline_sql_to_datalab(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/migrate.db")
    factory = make_session_factory(engine)
    legacy = SqlExperimentStore(factory)
    legacy.add([_record()])

    state = MockDatalabState()
    client = httpx.Client(
        base_url="http://datalab.test",
        transport=httpx.MockTransport(_mock_handler(state)),
    )
    datalab = DatalabExperimentStore("http://datalab.test", factory)
    datalab._client = client
    try:
        migrated = migrate_inline_sql_to_datalab(legacy, datalab)
        assert migrated == 1
        assert legacy.count() == 0
        assert datalab.count() == 1
        assert datalab.all()[0].measured["salt_spray_hours"] == 840.0
    finally:
        datalab.close()


def test_migrate_skips_when_json_absent(tmp_path):
    engine = make_engine("sqlite:///:memory:")
    sql_store = SqlExperimentStore(make_session_factory(engine))
    migrated = migrate_from_stores(str(tmp_path / "no_such_file.json"), sql_store)
    assert migrated == 0
