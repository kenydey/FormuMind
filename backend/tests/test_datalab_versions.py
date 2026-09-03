"""P3/P4: datalab_client 版本历史与文件读回（mock transport 单测）。"""
from __future__ import annotations

import httpx

from app.db.datalab_client import diff_item_versions, get_file_bytes, list_item_versions

_BASE = "http://datalab.test"


def _versions_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/items/test:ABC123/versions/":
        return httpx.Response(
            200,
            json={
                "status": "success",
                "versions": [
                    {
                        "_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
                        "version": 2,
                        "action": "edited",
                        "timestamp": "2026-09-02T10:00:00",
                        "creator": {"display_name": "Alice"},
                    },
                    {
                        "_id": "bbbbbbbbbbbbbbbbbbbbbbbb",
                        "version": 1,
                        "action": "created",
                        "timestamp": "2026-08-30T11:14:41",
                        "creator": None,
                    },
                ],
            },
        )
    if path == "/items/test:ABC123/compare-versions/":
        return httpx.Response(
            200,
            json={
                "status": "success",
                "diff": {"values_changed": {"root['x']": {"new_value": 2, "old_value": 1}}},
            },
        )
    if path == "/files/cccccccccccccccccccccccc/qc.txt":
        return httpx.Response(200, content=b"QC-CERTIFICATE-BYTES")
    return httpx.Response(404, json={"error": f"unmocked {path}"})


def _transport() -> httpx.MockTransport:
    return httpx.MockTransport(_versions_handler)


def test_list_item_versions_happy_path():
    versions = list_item_versions(_BASE, "test:ABC123", _transport=_transport())
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[0]["action"] == "edited"
    assert versions[0]["creator"] == "Alice"
    assert versions[1]["id"] == "bbbbbbbbbbbbbbbbbbbbbbbb"
    assert versions[1]["version"] == 1


def test_list_item_versions_failure_paths():
    # empty api_url / empty refcode -> []
    assert list_item_versions("", "test:ABC123") == []
    assert list_item_versions(_BASE, "") == []
    # transport that 500s -> [] (degrade, never raise)
    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    assert list_item_versions(_BASE, "test:ABC123", _transport=httpx.MockTransport(boom)) == []


def test_diff_item_versions_happy_path():
    diff = diff_item_versions(
        _BASE, "test:ABC123", "aaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbb",
        _transport=_transport(),
    )
    assert "values_changed" in diff


def test_diff_item_versions_failure_paths():
    assert diff_item_versions(_BASE, "", "a", "b") == {}
    assert diff_item_versions(_BASE, "test:ABC123", "", "") == {}


def test_get_file_bytes_happy_path():
    content = get_file_bytes(_BASE, "cccccccccccccccccccccccc", "qc.txt", _transport=_transport())
    assert content == b"QC-CERTIFICATE-BYTES"


def test_get_file_bytes_failure_paths():
    assert get_file_bytes("", "cccccccccccccccccccccccc", "qc.txt") is None
    assert get_file_bytes(_BASE, "", "qc.txt") is None

    def not_found(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "nope"})

    assert get_file_bytes(_BASE, "cccccccccccccccccccccccc", "qc.txt", _transport=httpx.MockTransport(not_found)) is None


def test_restore_item_version_happy_path():
    from app.db.datalab_client import restore_item_version

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items/test:ABC123/restore-version/"
        import json

        assert json.loads(request.content) == {"version_id": "aaaaaaaaaaaaaaaaaaaaaaaa"}
        return httpx.Response(200, json={"status": "success"})

    ok = restore_item_version(
        _BASE, "test:ABC123", "aaaaaaaaaaaaaaaaaaaaaaaa",
        _transport=httpx.MockTransport(handler),
    )
    assert ok is True


def test_restore_item_version_failure_paths():
    from app.db.datalab_client import restore_item_version

    assert restore_item_version("", "test:ABC123", "aaa") is False
    assert restore_item_version(_BASE, "", "aaa") is False
    assert restore_item_version(_BASE, "test:ABC123", "") is False

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "version gone"})

    assert (
        restore_item_version(
            _BASE, "test:ABC123", "aaaaaaaaaaaaaaaaaaaaaaaa",
            _transport=httpx.MockTransport(boom),
        )
        is False
    )


def test_delete_attachment_store(tmp_path):
    """P4: delete_attachment unbinds the local row (platform copy kept)."""
    from app.db.database import make_engine, make_session_factory
    from app.db.measurement_store import MeasurementStore
    from app.db.models import Base, ExperimentAttachment, ExperimentRow, SourceDocument

    engine = make_engine(f"sqlite:///{tmp_path / 'att.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = MeasurementStore(factory)

    with factory() as session:
        exp = ExperimentRow(
            id=1,
            item_id="test-item",
            label="test:1",
            source="test",
            domain="anticorrosion_coating",
            measured={},
        )
        session.add(exp)
        doc = SourceDocument(
            id="local-deadbeef",
            filename="qc.txt",
            source_kind="qc_report",
            content_hash="deadbeef",
        )
        session.add(doc)
        session.commit()

    with factory() as session:
        att = ExperimentAttachment(
            id="test-attach-0001",
            experiment_id=1,
            source_document_id="local-deadbeef",
            kind="qc_report",
            note="[datalab:6a984f3fc6651cf90cfa6931]",
        )
        session.add(att)
        session.commit()
        att_id = att.id

    assert store.delete_attachment(str(att_id)) is True
    assert store.delete_attachment(str(att_id)) is False  # 已删
    with factory() as session:
        assert session.get(ExperimentAttachment, att_id) is None
