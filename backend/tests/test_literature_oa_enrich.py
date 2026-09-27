"""Wave D — literature OA enrich into manifest."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import literature_oa_enrich as oe
from app.services.fulltext_fetcher import FulltextReport


class _Settings(SimpleNamespace):
    literature_manifest_enabled = True
    literature_oa_enrich_enabled = True


def test_oa_enrich_flag_off(tmp_path, monkeypatch):
    monkeypatch.setattr(oe.lm, "_data_root", lambda: tmp_path)
    man = oe.lm.empty_manifest("p1")
    man["items"] = [{"id": "10.1/x", "doi": "10.1/x", "title": "t", "screening": "unset"}]
    oe.lm.save_manifest(man)
    try:
        oe.enrich_manifest_oa(
            "p1",
            settings=_Settings(literature_oa_enrich_enabled=False),
        )
        assert False, "expected PermissionError"
    except PermissionError:
        pass


def test_oa_enrich_success_unfreezes(tmp_path, monkeypatch):
    monkeypatch.setattr(oe.lm, "_data_root", lambda: tmp_path)
    man = oe.lm.empty_manifest("p1")
    man["items"] = [
        {
            "id": "10.1234/abc",
            "doi": "10.1234/abc",
            "title": "Epoxy paper",
            "screening": "unset",
        }
    ]
    man["frozen"] = {
        "at": 1.0,
        "actor": "t",
        "item_ids": ["10.1234/abc"],
        "digest": "x",
    }
    oe.lm.save_manifest(man)

    report = FulltextReport()
    report.record("literature", True)

    with patch.object(oe, "classify", return_value="literature"), patch.object(
        oe, "enrich_search_results", return_value=([], report)
    ) as mock_enrich:
        out = oe.enrich_manifest_oa("p1", limit=5, settings=_Settings())
        assert mock_enrich.called
        assert out["fetched"] == 1
        assert out["persisted"] == 1
        assert out["manifest"]["frozen"] is None
        item = out["manifest"]["items"][0]
        assert item["has_fulltext"] is True
        assert item["enrich_status"] == "fetched"


def test_oa_enrich_fetch_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(oe.lm, "_data_root", lambda: tmp_path)
    man = oe.lm.empty_manifest("p1")
    man["items"] = [
        {"id": "10.9/z", "doi": "10.9/z", "title": "paywall", "screening": "unset"}
    ]
    oe.lm.save_manifest(man)
    report = FulltextReport()
    report.record("literature", False)
    with patch.object(oe, "classify", return_value="literature"), patch.object(
        oe, "enrich_search_results", return_value=([], report)
    ):
        out = oe.enrich_manifest_oa("p1", settings=_Settings())
        assert out["fetched"] == 0
        assert out["skipped"] == 1
        assert out["failures"]
