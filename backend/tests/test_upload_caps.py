"""Every multipart upload is read through one capped reader (B-17 follow-up).

Before: only ``/api/ingest/*`` streamed with a cap. QC reports, MCP-config
import, materials import, skill install, project export shelf and the three
experiment endpoints did ``await file.read()`` first (the experiment endpoints
and QC checked a size *afterwards*, the rest never), and the experiment
attachments hard-coded 20 MB instead of ``ingest_max_upload_bytes``.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api._uploads import read_upload_capped, upload_limit_bytes
from app.config import get_settings
from app.main import app

LIMIT = 2048
BIG = b"x" * (LIMIT + 1)


class _Upload:
    def __init__(self, chunks, size=None):
        self._chunks = list(chunks)
        self.size = size
        self.reads = 0

    async def read(self, n=-1):
        self.reads += 1
        return self._chunks.pop(0) if self._chunks else b""


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_INGEST_MAX_UPLOAD_BYTES", str(LIMIT))
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


# ── the helper ──────────────────────────────────────────────────────────────


def test_limit_comes_from_settings(client):
    assert upload_limit_bytes() == LIMIT


def test_reads_fully_under_the_cap():
    up = _Upload([b"a" * 1000, b"b" * 1000])
    assert asyncio.run(read_upload_capped(up, "ok.bin", limit=LIMIT)) == b"a" * 1000 + b"b" * 1000


def test_stops_reading_as_soon_as_the_cap_is_passed():
    up = _Upload([b"x" * 1024] * 10)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(read_upload_capped(up, "big.bin", limit=LIMIT))
    assert ei.value.status_code == 413
    assert up.reads == 3, "must not drain the rest of the body"


def test_known_oversize_is_rejected_without_reading():
    up = _Upload([b"x"], size=LIMIT + 1)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(read_upload_capped(up, "big.bin", limit=LIMIT))
    assert ei.value.status_code == 413 and up.reads == 0


def test_exact_cap_passes():
    up = _Upload([b"y" * LIMIT])
    assert len(asyncio.run(read_upload_capped(up, "edge.bin", limit=LIMIT))) == LIMIT


# ── the endpoints ───────────────────────────────────────────────────────────


def _files(name="f.bin"):
    return {"file": (name, BIG, "application/octet-stream")}


@pytest.mark.parametrize(
    "path",
    [
        "/api/experiments/1/attachments",
        "/api/experiments/workbench/1/rows/1/attachments",
        "/api/experiments/import-csv",
        "/api/qc/report",
        "/api/connectors/mcp/import/upload",
        "/api/skills/install/upload",
        "/api/materials/import",
    ],
)
def test_oversized_upload_is_413_on_every_endpoint(client, monkeypatch, path):
    if "workbench" in path:
        # resolving the row's experiment happens first; short-circuit it.
        import app.api.experiments as exp

        async def _resolved(*_a, **_k):
            return 1

        monkeypatch.setattr(exp, "_resolve_workbench_experiment_id", _resolved)
    res = client.post(path, files=_files())
    assert res.status_code == 413, (path, res.status_code, res.text[:200])
    assert "upload limit" in res.json()["detail"]


def test_project_export_upload_uses_the_smaller_shelf_cap(client, monkeypatch):
    from app.services import project_exports

    monkeypatch.setattr(project_exports, "MAX_EXPORT_BYTES", 512)
    monkeypatch.setattr(project_exports, "ensure_project_exists", lambda pid: None)
    # 1 KiB is well under ingest_max_upload_bytes (2 KiB here) but over the shelf cap.
    res = client.post(
        "/api/projects/p1/exports/upload",
        files={"file": ("a.pdf", b"z" * 1024, "application/pdf")},
    )
    assert res.status_code == 413, res.text


def test_attachment_cap_follows_the_setting_not_a_hardcoded_20mb(client, monkeypatch):
    """A file above ingest_max_upload_bytes is refused even though it is far
    below the old hard-coded 20 MB."""
    assert LIMIT < 20 * 1024 * 1024
    res = client.post("/api/experiments/1/attachments", files=_files())
    assert res.status_code == 413
