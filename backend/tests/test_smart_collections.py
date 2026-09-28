"""W6-3 / P2-3 Smart Collections: CRUD, refresh diff, schedule, snapshot cap."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.services import smart_collections as sc
from app.services import literature_manifest as lm


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    return tmp_path


def _ev(identifier: str, title: str | None = None):
    return SimpleNamespace(
        identifier=identifier,
        title=title or f"Title of {identifier}",
        snippet=f"snippet for {identifier}",
        doi=None,
        source="openalex",
    )


@pytest.fixture()
def search_hits(monkeypatch):
    """Swap the real federated search for a controllable stub."""
    state = {"evidence": []}

    def fake_run_search(query, filters, settings=None):
        state["query"] = query
        state["filters"] = dict(filters)
        return list(state["evidence"])

    monkeypatch.setattr(sc, "_run_search", fake_run_search)
    return state


# ── CRUD ────────────────────────────────────────────────────────────────────


def test_crud_lifecycle(data_dir):
    col = sc.create_collection(
        "p1",
        name="VIANT papers",
        query="waterborne conversion coating",
        filters={"date_from": 2020, "domain_allowlist": ["sciencedirect.com"]},
        screening_preset="corrosion_coating",
        schedule={"enabled": True, "interval_hours": 48},
    )
    assert col["collection_id"].startswith("col_")
    assert col["filters"] == {"date_from": 2020, "domain_allowlist": ["sciencedirect.com"]}
    assert col["screening_preset"] == "corrosion_coating"
    assert col["schedule"]["interval_hours"] == 48

    listed = sc.list_collections("p1")
    assert len(listed) == 1
    assert listed[0]["name"] == "VIANT papers"
    assert listed[0]["snapshot_count"] == 0

    got = sc.get_collection("p1", col["collection_id"])
    assert got["query"] == "waterborne conversion coating"

    updated = sc.update_collection(
        "p1", col["collection_id"], name="VIANT v2", schedule={"enabled": False}
    )
    assert updated["name"] == "VIANT v2"
    assert updated["schedule"]["enabled"] is False
    assert updated["schedule"]["interval_hours"] == 48  # preserved

    assert sc.delete_collection("p1", col["collection_id"]) is True
    assert sc.list_collections("p1") == []
    assert sc.delete_collection("p1", col["collection_id"]) is False


def test_create_validation(data_dir):
    with pytest.raises(ValueError):
        sc.create_collection("p1", name="", query="x")
    with pytest.raises(ValueError):
        sc.create_collection("p1", name="n", query="  ")
    with pytest.raises(ValueError):
        sc.update_collection("p1", "col_nope", name="")
    assert sc.get_collection("p1", "col_nope") is None
    assert sc.update_collection("p1", "col_nope", name="x") is None


# ── refresh: search → manifest merge → snapshot diff ────────────────────────


def test_refresh_merges_and_snapshots(data_dir, search_hits):
    col = sc.create_collection("p1", name="c", query="q")
    search_hits["evidence"] = [_ev("id-a"), _ev("id-b")]

    snap = sc.refresh_collection("p1", col["collection_id"])
    assert snap["total"] == 2
    assert snap["added"] == ["id-a", "id-b"]
    assert snap["removed"] == []
    assert snap["manifest_added"] == 2
    assert search_hits["query"] == "q"

    man = lm.load_manifest("p1")
    by_id = {i["id"]: i for i in man["items"]}
    assert by_id["id-a"]["screening_source"] == "collection"
    assert by_id["id-a"]["screening"] == "unset"

    # Second refresh: one removed, one added → entry-level diff.
    search_hits["evidence"] = [_ev("id-b"), _ev("id-c")]
    snap2 = sc.refresh_collection("p1", col["collection_id"])
    assert snap2["added"] == ["id-c"]
    assert snap2["removed"] == ["id-a"]
    assert snap2["total"] == 2
    # Removed collection items stay in the manifest (refresh never deletes
    # manifest items — it only adds candidates).
    man2 = lm.load_manifest("p1")
    assert {i["id"] for i in man2["items"]} == {"id-a", "id-b", "id-c"}

    detail = sc.get_collection("p1", col["collection_id"])
    assert len(detail["snapshots"]) == 2


def test_refresh_passes_filters_to_search(data_dir, search_hits):
    col = sc.create_collection(
        "p1",
        name="c",
        query="q",
        filters={"date_from": 2021, "date_to": 2025, "domain_allowlist": "a.com, b.com"},
    )
    sc.refresh_collection("p1", col["collection_id"])
    assert search_hits["filters"]["date_from"] == 2021
    assert search_hits["filters"]["date_to"] == 2025
    assert search_hits["filters"]["domain_allowlist"] == ["a.com", "b.com"]


def test_refresh_preserves_human_disposition(data_dir, search_hits):
    """P0-8: human screening decisions are never overwritten by refresh."""
    col = sc.create_collection("p1", name="c", query="q")
    search_hits["evidence"] = [_ev("id-a")]
    sc.refresh_collection("p1", col["collection_id"])

    man = lm.load_manifest("p1")
    man["items"][0]["screening"] = "no_match"
    man["items"][0]["screening_source"] = "human"
    lm.save_manifest(man)

    search_hits["evidence"] = [_ev("id-a"), _ev("id-b")]
    snap = sc.refresh_collection("p1", col["collection_id"])
    assert snap["manifest_added"] == 1  # only id-b is new

    man2 = lm.load_manifest("p1")
    by_id = {i["id"]: i for i in man2["items"]}
    assert by_id["id-a"]["screening"] == "no_match"
    assert by_id["id-a"]["screening_source"] == "human"


def test_refresh_unknown_collection(data_dir, search_hits):
    with pytest.raises(KeyError):
        sc.refresh_collection("p1", "col_missing")


# ── schedule ────────────────────────────────────────────────────────────────


def test_is_due_logic(data_dir):
    col = sc.create_collection("p1", name="c", query="q")
    # Never refreshed → due.
    assert sc.is_due(col) is True

    col["schedule"]["last_run"] = time.time()
    assert sc.is_due(col) is False

    col["schedule"]["last_run"] = time.time() - 25 * 3600
    assert sc.is_due(col) is True  # past the 24h default interval

    col["schedule"]["enabled"] = False
    assert sc.is_due(col) is False


def test_refresh_due_collections_sweeps_only_due(data_dir, search_hits):
    due = sc.create_collection("p1", name="due", query="q1")
    not_due = sc.create_collection(
        "p1", name="skip", query="q2", schedule={"enabled": False}
    )
    search_hits["evidence"] = [_ev("x")]
    results = sc.refresh_due_collections("p1")
    assert [r["collection_id"] for r in results] == [due["collection_id"]]
    assert all(r["ok"] for r in results)
    # Second sweep immediately after: nothing due.
    assert sc.refresh_due_collections("p1") == []


# ── snapshot cap ────────────────────────────────────────────────────────────


def test_snapshot_cap_keeps_newest_ten(data_dir, search_hits):
    col = sc.create_collection("p1", name="c", query="q")
    search_hits["evidence"] = [_ev("id-a")]
    for i in range(12):
        search_hits["evidence"] = [_ev(f"id-{i}")]
        sc.refresh_collection("p1", col["collection_id"])
    detail = sc.get_collection("p1", col["collection_id"])
    snaps = detail["snapshots"]
    assert len(snaps) == 10
    # Newest kept: last snapshot has item id-11, oldest kept has id-2.
    assert snaps[-1]["item_ids"] == ["id-11"]
    assert snaps[0]["item_ids"] == ["id-2"]
