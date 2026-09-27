"""Wave D2 — lightweight RO-Crate steel-stamp export."""
from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import ro_crate_export as rc


class _Settings(SimpleNamespace):
    ro_crate_export_enabled = True
    literature_manifest_enabled = True


def test_ro_crate_flag_off():
    with pytest.raises(PermissionError):
        rc.build_lightweight_ro_crate(
            "p1",
            settings=_Settings(ro_crate_export_enabled=False),
        )


def test_ro_crate_zip_contents():
    row = MagicMock()
    row.path = "reports/project-p1-storm.md"
    row.title = "STORM"
    row.source_ids = ["s1"]
    row.flags = []
    row.updated_at = None

    store = MagicMock()
    store.get_by_path.return_value = row
    store.read_markdown.return_value = (
        "Cure at 120 °C.[^1]\n\n[^1]: Source: doc.pdf, pp. 3\n"
    )

    man = {
        "project_id": "p1",
        "items": [{"id": "a", "title": "t"}],
        "frozen": {"item_ids": ["a"], "digest": "d", "at": 1.0, "actor": "t"},
        "coverage": {"candidate_count": 1, "frozen_count": 1},
    }

    with (
        patch("app.db.wiki_store.get_wiki_store", return_value=store),
        patch("app.services.literature_manifest.load_manifest", return_value=man),
        patch("app.services.literature_manifest.manifest_enabled", return_value=True),
        patch(
            "app.services.publication_preflight.get_state",
            return_value={
                "project_id": "p1",
                "kind": "storm",
                "findings": [],
                "open_blocking": 0,
                "open_major": 0,
                "ready": True,
            },
        ),
    ):
        raw, filename = rc.build_lightweight_ro_crate("p1", settings=_Settings())

    assert filename.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
        assert "ro-crate-metadata.json" in names
        assert "report.md" in names
        assert "literature-manifest.json" in names
        assert "preflight.json" in names
        assert "steel-stamp.json" in names
        meta = json.loads(zf.read("ro-crate-metadata.json"))
        assert meta["@context"] == rc.RO_CRATE_CONTEXT
        stamp = json.loads(zf.read("steel-stamp.json"))
        assert stamp["honesty"]["replay"] == "not claimed"
