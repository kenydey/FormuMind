"""QC report ingest: typed measurements bound to an experiment.

Covers the three things that were missing: measurements that carry the context
making them comparable (unit, test method, spec window), a hard link from a
report back to the run it measured, and referential integrity that is actually
enforced rather than merely declared.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.database import Base, make_engine, make_session_factory
from app.db.measurement_store import MeasurementStore
from app.db.models import ExperimentAttachment, ExperimentRow, MeasurementRow, SourceDocument
from app.db.session_utils import commit_session
from app.domain.schemas import ExperimentRecord, Measurement, ProductDomain
from app.main import app
from app.services.qc_report import QCMeasurement, QCReportExtraction, extract_qc_report

client = TestClient(app)

_REPORT = b"""# Test Report TR-2026-0417

| Item | Method | Spec | Result | Unit |
|---|---|---|---|---|
| Neutral salt spray | ASTM B117 | >= 500 | 720 | h |
| Adhesion | GB/T 5210 | >= 5.0 | 3.2 | MPa |

Laboratory: Shanghai Coatings Test Center
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated DB, with both the store singleton and the default session
    factory pointed at it — the endpoint resolves its own factory, so patching
    only the store would leave writes and reads on different databases."""
    import app.db.database as database_mod
    import app.db.measurement_store as measurement_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/qc.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = MeasurementStore(factory)
    monkeypatch.setattr(measurement_store_mod, "_store", store)
    monkeypatch.setattr(database_mod, "default_session_factory", lambda: factory)
    return factory, store


@pytest.fixture()
def experiment(db):
    factory, _ = db
    with commit_session(factory) as session:
        row = ExperimentRow(
            domain="anticorrosion_coating", factors={"Zinc phosphate": 8.0},
            measured={"salt_spray_hours": 700.0}, source="lab", label="qc-test",
        )
        session.add(row)
        session.flush()
        return row.id


# ── the domain type ──────────────────────────────────────────────────────────


def test_measurement_derives_pass_from_spec_window():
    assert Measurement(metric="m", value=720, spec_min=500).passed is True
    assert Measurement(metric="m", value=3.2, spec_min=5.0).passed is False
    assert Measurement(metric="m", value=8.0, spec_min=6, spec_max=10).passed is True
    assert Measurement(metric="m", value=11.0, spec_min=6, spec_max=10).passed is False


def test_measurement_without_spec_has_no_verdict():
    """No acceptance window means no pass/fail — not a silent pass."""
    assert Measurement(metric="m", value=720).passed is None


def test_explicit_passed_is_not_overridden():
    assert Measurement(metric="m", value=1.0, spec_min=99.0, passed=True).passed is True


# ── backward compatibility of ExperimentRecord ───────────────────────────────


def test_legacy_measured_dict_still_accepted():
    """Every existing caller, JSON file and Datalab block passes `measured`."""
    record = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating, measured={"salt_spray_hours": 840.0}
    )
    assert record.measured == {"salt_spray_hours": 840.0}
    assert len(record.measurements) == 1
    assert record.measurements[0].metric == "salt_spray_hours"


def test_measured_survives_model_dump_roundtrip():
    """The JSON store and Datalab writer both serialise records."""
    record = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating, measured={"a": 1.0, "b": 2.0}
    )
    dumped = record.model_dump()
    assert dumped["measured"] == {"a": 1.0, "b": 2.0}
    assert ExperimentRecord(**dumped).measured == {"a": 1.0, "b": 2.0}


def test_typed_measurements_project_to_measured():
    record = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        measurements=[
            Measurement(metric="salt_spray_hours", value=720, unit="h", test_method="ASTM B117")
        ],
    )
    assert record.measured == {"salt_spray_hours": 720.0}
    assert record.measurement_for("salt_spray_hours").test_method == "ASTM B117"
    assert record.measurement_for("nope") is None


def test_non_numeric_legacy_values_are_dropped_not_crashed():
    record = ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        measured={"good": 1.0, "bad": None, "flag": True},
    )
    assert record.measured == {"good": 1.0}


# ── referential integrity is real ────────────────────────────────────────────


def test_sqlite_foreign_keys_are_enforced(db):
    """SQLite ignores FKs unless the pragma is set per connection — without it
    dev and CI would accept orphans that PostgreSQL rejects in production."""
    factory, _ = db
    with factory() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_measurement_with_unknown_experiment_is_rejected(db):
    factory, _ = db
    with pytest.raises(IntegrityError):
        with commit_session(factory) as session:
            session.add(
                MeasurementRow(id="m1", experiment_id=999999, metric="x", value=1.0)
            )


def test_deleting_an_experiment_cascades_to_measurements(db, experiment):
    factory, store = db
    store.add(experiment, [Measurement(metric="salt_spray_hours", value=720)])
    assert len(store.for_experiment(experiment)) == 1

    with commit_session(factory) as session:
        session.delete(session.get(ExperimentRow, experiment))
    assert store.for_experiment(experiment) == []


def test_deleting_a_report_keeps_the_measurement(db, experiment):
    """SET NULL, not CASCADE: losing the certificate must not delete the result."""
    factory, store = db
    with commit_session(factory) as session:
        session.add(
            SourceDocument(id="doc-1", filename="r.pdf", content_hash="h", source_kind="qc_report")
        )
    store.add(experiment, [Measurement(metric="salt_spray_hours", value=720)],
              source_document_id="doc-1")

    with commit_session(factory) as session:
        session.delete(session.get(SourceDocument, "doc-1"))

    remaining = store.for_experiment(experiment)
    assert len(remaining) == 1
    assert remaining[0].source_document_id is None


def test_attachment_is_idempotent(db, experiment):
    factory, store = db
    with commit_session(factory) as session:
        session.add(
            SourceDocument(id="doc-2", filename="r.pdf", content_hash="h2", source_kind="qc_report")
        )
    assert store.attach(experiment, "doc-2") is not None
    assert store.attach(experiment, "doc-2") is None  # already bound
    assert len(store.attachments_for(experiment)) == 1


def test_store_reports_failing_measurements(db, experiment):
    _, store = db
    store.add(experiment, [
        Measurement(metric="salt_spray_hours", value=720, spec_min=500),
        Measurement(metric="adhesion_mpa", value=3.2, spec_min=5.0),
    ])
    failing = store.failing(experiment)
    assert [m.metric for m in failing] == ["adhesion_mpa"]


# ── LLM extraction ───────────────────────────────────────────────────────────


def test_extraction_degrades_without_an_llm():
    """No API key must yield a usable object, not an exception."""
    extraction, err = extract_qc_report("some report text", title="r")
    assert isinstance(extraction, QCReportExtraction)
    assert extraction.measurements == []
    assert err


def test_extraction_maps_to_domain_measurements(monkeypatch):
    from app.services import qc_report as qc_module

    payload = QCReportExtraction(
        report_id="TR-2026-0417",
        laboratory="Shanghai Coatings Test Center",
        measurements=[
            QCMeasurement(metric="salt_spray_hours", value=720, unit="h",
                          test_method="ASTM B117", spec_min=500),
            QCMeasurement(metric="adhesion_mpa", value=3.2, unit="MPa",
                          test_method="GB/T 5210", spec_min=5.0),
        ],
    )
    monkeypatch.setattr(qc_module, "complete_structured", lambda *a, **k: (payload, None))

    extraction, err = extract_qc_report("report", title="r")
    assert err is None
    measurements = extraction.to_measurements(source_document_id="doc-9")
    assert [m.metric for m in measurements] == ["salt_spray_hours", "adhesion_mpa"]
    assert measurements[0].test_method == "ASTM B117"
    assert measurements[0].passed is True
    assert measurements[1].passed is False  # 3.2 below the 5.0 floor
    assert all(m.source_document_id == "doc-9" for m in measurements)


def test_extraction_skips_rows_without_a_metric():
    extraction = QCReportExtraction(
        measurements=[QCMeasurement(metric="   ", value=1.0),
                      QCMeasurement(metric="ok", value=2.0)]
    )
    assert [m.metric for m in extraction.to_measurements()] == ["ok"]


def test_empty_document_degrades():
    extraction, err = extract_qc_report("", title="r")
    assert extraction.confidence == 0.0
    assert err


# ── the transaction ──────────────────────────────────────────────────────────


def test_ingest_binds_report_and_measurements(db, experiment, monkeypatch):
    from app.services import qc_report as qc_module
    from app.services.qc_ingest import ingest_qc_report_tx

    factory, store = db
    payload = QCReportExtraction(
        measurements=[QCMeasurement(metric="salt_spray_hours", value=720, unit="h",
                                    test_method="ASTM B117", spec_min=500)]
    )
    monkeypatch.setattr(qc_module, "complete_structured", lambda *a, **k: (payload, None))

    result = ingest_qc_report_tx(
        factory, experiment_id=experiment, filename="TR.md", text="report body", title="TR"
    )
    assert result.measurement_count == 1
    assert not result.already_attached
    assert len(store.for_experiment(experiment)) == 1
    assert len(store.attachments_for(experiment)) == 1
    assert store.experiments_for_document(result.source_id) == [experiment]


def test_reingesting_the_same_report_does_not_double_count(db, experiment, monkeypatch):
    """Two uploads of one certificate is one result, not two."""
    from app.services import qc_report as qc_module
    from app.services.qc_ingest import ingest_qc_report_tx

    factory, store = db
    payload = QCReportExtraction(
        measurements=[QCMeasurement(metric="salt_spray_hours", value=720)]
    )
    monkeypatch.setattr(qc_module, "complete_structured", lambda *a, **k: (payload, None))

    first = ingest_qc_report_tx(factory, experiment_id=experiment, filename="TR.md", text="body")
    second = ingest_qc_report_tx(factory, experiment_id=experiment, filename="TR.md", text="body")

    assert second.source_id == first.source_id
    assert second.already_attached
    assert len(store.for_experiment(experiment)) == 1
    assert len(store.attachments_for(experiment)) == 1


def test_ingest_rejects_unknown_experiment(db):
    from app.services.qc_ingest import ingest_qc_report_tx

    factory, _ = db
    with pytest.raises(ValueError, match="not found"):
        ingest_qc_report_tx(factory, experiment_id=999999, filename="x.md", text="body")


# ── workbench-row binding (P3 regression: ensure_experiment_for_row) ─────────


class _FakeCampaign:
    def __init__(self):
        self.id = 42
        self.project_id = "proj_qc"
        self.owner_id = None


def test_qc_report_binds_workbench_row_placeholder(db, monkeypatch):
    """POST /api/qc/report with campaign_id+row_id must create the ExperimentRow
    placeholder and bind measurements — regression for the un-flushed refresh
    crash (InvalidRequestError) that only fired on real datalab-mode binding."""
    from app.db.campaign_types import WorkbenchRow
    from app.services import qc_report as qc_module
    from app.services.qc_ingest import ingest_qc_report_tx

    factory, _ = db

    class _FakeStore:
        def list_rows_sync(self, campaign_id):
            assert campaign_id == 42
            return [WorkbenchRow(
                id=1, campaign_id=42, item_id="formumind_c42_r1_hash",
                status="Pending", planned_params={"Zinc phosphate": 9.0},
                actual_params={}, measurements={},
            )]

        def get_campaign_sync(self, campaign_id):
            return _FakeCampaign()

    monkeypatch.setattr(
        "app.db.campaign_store.get_campaign_store",
        lambda: _FakeStore(),
    )
    payload = QCReportExtraction(
        measurements=[QCMeasurement(metric="salt_spray_hours", value=720, unit="h")]
    )
    monkeypatch.setattr(qc_module, "complete_structured", lambda *a, **k: (payload, None))

    resp = client.post(
        "/api/qc/report",
        data={"campaign_id": "42", "row_id": "1", "sync_measured": "false"},
        files={"file": ("TR.md", io.BytesIO(b"# TR\nsalt spray 720 h"), "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["experiment_id"] > 0
    assert body["measurement_count"] == 1
    assert body["synced_measured"] == {}

    # The placeholder row must exist and be labelled for the workbench row.
    with factory() as session:
        row = (
            session.query(ExperimentRow)
            .filter(ExperimentRow.id == body["experiment_id"])
            .first()
        )
        assert row is not None
        assert row.label == "wb:42:formumind_c42_r1_hash"
        assert row.item_id is None  # placeholder: not yet ingested into training



def test_measurements_sync_into_the_trainable_column(db, experiment, monkeypatch):
    """A certificate only becomes training data once it reaches `measured`."""
    import app.db.database as database_mod
    from app.services.qc_ingest import sync_measurements_to_experiment

    factory, store = db
    monkeypatch.setattr(database_mod, "default_session_factory", lambda: factory)
    store.add(experiment, [Measurement(metric="salt_spray_hours", value=720, unit="h")])

    merged = sync_measurements_to_experiment(experiment)
    assert merged["salt_spray_hours"] == 720.0  # overrides the earlier 700.0
    with factory() as session:
        assert session.get(ExperimentRow, experiment).measured["salt_spray_hours"] == 720.0


# ── endpoints ────────────────────────────────────────────────────────────────


def test_report_endpoint_stores_and_binds(db, experiment):
    response = client.post(
        "/api/qc/report",
        files={"file": ("TR.md", io.BytesIO(_REPORT), "text/markdown")},
        data={"experiment_id": str(experiment)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["experiment_id"] == experiment
    assert body["source_id"]
    # Which tier wins depends on what is installed — markitdown handles .md
    # when present, the plain decoder otherwise. Pinning one name made this
    # assert the environment rather than the behaviour; what matters is that
    # a real parser ran and recorded itself.
    assert body["parser"] in {"markitdown", "text"}

    listing = client.get(f"/api/qc/experiments/{experiment}/measurements")
    assert listing.status_code == 200
    assert len(listing.json()["attachments"]) == 1


def test_report_endpoint_404_for_unknown_experiment(db):
    response = client.post(
        "/api/qc/report",
        files={"file": ("TR.md", io.BytesIO(_REPORT), "text/markdown")},
        data={"experiment_id": "999999"},
    )
    assert response.status_code == 404


def test_report_endpoint_rejects_empty_file(db, experiment):
    response = client.post(
        "/api/qc/report",
        files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
        data={"experiment_id": str(experiment)},
    )
    assert response.status_code == 400


def test_report_endpoint_422_when_nothing_parses(db, experiment):
    response = client.post(
        "/api/qc/report",
        files={"file": ("x.bin", io.BytesIO(b"\x00\x01\x02"), "application/octet-stream")},
        data={"experiment_id": str(experiment)},
    )
    assert response.status_code == 422
