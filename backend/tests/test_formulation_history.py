"""Formulation revision history.

Snapshots alone do not answer "what changed between v3 and v4" — a reviewer
should not have to read two ingredient tables side by side to find the one
component that moved. The diff is the point, so most of this pins the diff.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import FormulationVersion
from app.db.session_utils import commit_session
from app.domain.schemas import Formulation, Ingredient, ProductDomain
from app.main import app
from app.services.formulation_history import (
    FormulationHistoryStore,
    describe_diff,
    diff_snapshots,
)

client = TestClient(app)


def _form(name: str = "Primer", **ratios: float) -> Formulation:
    ratios = ratios or {"Epoxy": 60.0, "Talc": 40.0}
    return Formulation(
        name=name,
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[
            Ingredient(name=n, role="resin" if n == "Epoxy" else "filler", weight_pct=v)
            for n, v in ratios.items()
        ],
        predicted={"salt_spray_hours": 800.0, "cost_cny_per_kg": 20.0},
    )


@pytest.fixture()
def store(tmp_path, monkeypatch):
    import app.services.formulation_history as history_mod

    engine = make_engine(f"sqlite:///{tmp_path}/history.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    st = FormulationHistoryStore(factory)
    monkeypatch.setattr(history_mod, "_store", st)
    return st


# ── the diff ─────────────────────────────────────────────────────────────────


def test_diff_classifies_added_removed_and_adjusted():
    before = _form(Epoxy=60.0, Talc=40.0).model_dump(mode="json")
    after = _form(Epoxy=55.0, **{"Zinc phosphate": 45.0}).model_dump(mode="json")
    diff = diff_snapshots(before, after)

    by_name = {c.name: c for c in diff.ingredient_changes}
    assert by_name["Talc"].change == "removed"
    assert by_name["Zinc phosphate"].change == "added"
    assert by_name["Epoxy"].change == "adjusted"
    assert by_name["Epoxy"].delta_pct == pytest.approx(-5.0)


def test_topology_change_is_distinguished_from_rebalancing():
    """Re-balancing a recipe and swapping a component are different reviews."""
    rebalanced = diff_snapshots(
        _form(Epoxy=60.0, Talc=40.0).model_dump(mode="json"),
        _form(Epoxy=65.0, Talc=35.0).model_dump(mode="json"),
    )
    swapped = diff_snapshots(
        _form(Epoxy=60.0, Talc=40.0).model_dump(mode="json"),
        _form(Epoxy=60.0, **{"Fumed silica": 40.0}).model_dump(mode="json"),
    )
    assert not rebalanced.topology_changed
    assert swapped.topology_changed


def test_rounding_noise_is_not_reported_as_an_edit():
    """_balanced renormalises to 100%, so tiny drifts are not user changes."""
    before = _form(Epoxy=60.0, Talc=40.0).model_dump(mode="json")
    after = _form(Epoxy=60.00001, Talc=40.0).model_dump(mode="json")
    assert diff_snapshots(before, after).ingredient_changes == []


def test_identical_snapshots_produce_an_empty_diff():
    snapshot = _form().model_dump(mode="json")
    diff = diff_snapshots(snapshot, snapshot)
    assert diff.ingredient_changes == []
    assert describe_diff(diff) == "无成分变化"


def test_metric_deltas_include_percentage():
    before = _form().model_dump(mode="json")
    after = _form()
    after.predicted = {"salt_spray_hours": 1000.0, "cost_cny_per_kg": 20.0}
    diff = diff_snapshots(before, after.model_dump(mode="json"))
    assert diff.metric_deltas["salt_spray_hours"]["delta"] == pytest.approx(200.0)
    assert diff.metric_deltas["salt_spray_hours"]["pct"] == pytest.approx(25.0)
    assert diff.metric_deltas["cost_cny_per_kg"]["delta"] == pytest.approx(0.0)


def test_metric_present_on_one_side_only_is_not_a_delta_from_zero():
    before = _form()
    before.predicted = {"a": 1.0}
    after = _form()
    after.predicted = {"b": 2.0}
    deltas = diff_snapshots(before.model_dump(mode="json"), after.model_dump(mode="json")).metric_deltas
    assert deltas["a"]["delta"] is None
    assert deltas["b"]["before"] is None


def test_rename_is_reported():
    diff = diff_snapshots(
        _form(name="Primer v1").model_dump(mode="json"),
        _form(name="Primer v2").model_dump(mode="json"),
    )
    assert diff.renamed == ("Primer v1", "Primer v2")


def test_describe_diff_summarises_each_kind():
    diff = diff_snapshots(
        _form(Epoxy=60.0, Talc=40.0).model_dump(mode="json"),
        _form(Epoxy=50.0, **{"Zinc phosphate": 50.0}).model_dump(mode="json"),
    )
    summary = describe_diff(diff)
    assert "新增" in summary and "移除" in summary and "调整" in summary


# ── versioning ───────────────────────────────────────────────────────────────


def test_versions_increment_within_a_lineage(store):
    v1 = store.save_version(_form(), created_by="a")
    v2 = store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=v1.id)
    assert (v1.version, v2.version) == (1, 2)
    assert v2.lineage_id == v1.lineage_id
    assert v2.parent_version_id == v1.id


def test_saving_never_mutates_an_earlier_revision(store):
    """A revision is a new row. Otherwise the history stops being a history."""
    v1 = store.save_version(_form(Epoxy=60.0, Talc=40.0))
    store.save_version(_form(Epoxy=90.0, Talc=10.0), parent_version_id=v1.id)
    reloaded = store.get(v1.id)
    weights = {i["name"]: i["weight_pct"] for i in reloaded.snapshot["ingredients"]}
    assert weights["Epoxy"] == 60.0


def test_change_summary_is_generated_when_not_supplied(store):
    v1 = store.save_version(_form(Epoxy=60.0, Talc=40.0))
    v2 = store.save_version(
        _form(Epoxy=60.0, **{"Fumed silica": 40.0}), parent_version_id=v1.id
    )
    assert "新增" in v2.change_summary
    assert "Fumed silica" in v2.change_summary


def test_author_supplied_summary_wins(store):
    v1 = store.save_version(_form())
    v2 = store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=v1.id,
                            change_summary="raise binder for adhesion")
    assert v2.change_summary == "raise binder for adhesion"


def test_branches_share_a_lineage_but_not_a_line(store):
    """Two people exploring different directions from v1 must both be
    representable, rather than being flattened into one sequence."""
    root = store.save_version(_form())
    branch_a = store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=root.id)
    branch_b = store.save_version(_form(Epoxy=50.0, Talc=50.0), parent_version_id=root.id)

    assert branch_a.lineage_id == branch_b.lineage_id == root.lineage_id
    assert branch_a.parent_version_id == branch_b.parent_version_id == root.id
    assert len(store.lineage(root.lineage_id)) == 3


def test_ancestry_walks_the_actual_path_not_the_whole_lineage(store):
    root = store.save_version(_form())
    mid = store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=root.id)
    store.save_version(_form(Epoxy=50.0, Talc=50.0), parent_version_id=root.id)  # sibling
    leaf = store.save_version(_form(Epoxy=80.0, Talc=20.0), parent_version_id=mid.id)

    chain = [v.id for v in store.ancestry(leaf.id)]
    assert chain == [root.id, mid.id, leaf.id]


def test_lineage_is_ordered_and_latest_is_the_highest_version(store):
    v1 = store.save_version(_form())
    store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=v1.id)
    v3 = store.save_version(_form(Epoxy=80.0, Talc=20.0), parent_version_id=v1.id)
    assert [v.version for v in store.lineage(v1.lineage_id)] == [1, 2, 3]
    assert store.latest(v1.lineage_id).id == v3.id


def test_unknown_lineage_reads_empty(store):
    assert store.lineage("nope") == []
    assert store.latest("nope") is None
    assert store.get("nope") is None


def test_diff_versions_returns_none_for_unknown_ids(store):
    assert store.diff_versions("a", "b") is None


def test_deleting_a_parent_orphans_rather_than_erases(store, tmp_path):
    """SET NULL, not CASCADE — dropping one revision must not take its
    descendants with it."""
    v1 = store.save_version(_form())
    v2 = store.save_version(_form(Epoxy=70.0, Talc=30.0), parent_version_id=v1.id)

    with commit_session(store._session_factory) as session:
        session.delete(session.get(FormulationVersion, v1.id))

    survivor = store.get(v2.id)
    assert survivor is not None
    assert survivor.parent_version_id is None


# ── endpoints ────────────────────────────────────────────────────────────────


def test_save_and_read_back_a_lineage(store):
    response = client.post(
        "/api/formulations/versions",
        json={"formulation": _form().model_dump(mode="json"), "created_by": "wang"},
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["version"] == 1
    assert saved["created_by"] == "wang"

    lineage = client.get(f"/api/formulations/versions/{saved['lineage_id']}")
    assert lineage.status_code == 200
    assert [v["version"] for v in lineage.json()["versions"]] == [1]


def test_diff_endpoint_reports_the_change(store):
    first = client.post(
        "/api/formulations/versions",
        json={"formulation": _form(Epoxy=60.0, Talc=40.0).model_dump(mode="json")},
    ).json()
    second = client.post(
        "/api/formulations/versions",
        json={
            "formulation": _form(Epoxy=60.0, **{"Fumed silica": 40.0}).model_dump(mode="json"),
            "parent_version_id": first["id"],
        },
    ).json()

    diff = client.get(f"/api/formulations/versions/{first['id']}/diff/{second['id']}")
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["topology_changed"] is True
    changes = {c["name"]: c["change"] for c in body["ingredient_changes"]}
    assert changes["Talc"] == "removed"
    assert changes["Fumed silica"] == "added"


def test_detail_endpoint_returns_the_snapshot(store):
    saved = client.post(
        "/api/formulations/versions",
        json={"formulation": _form().model_dump(mode="json")},
    ).json()
    detail = client.get(f"/api/formulations/versions/detail/{saved['id']}")
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["ingredients"]


def test_unknown_lineage_and_diff_are_404(store):
    assert client.get("/api/formulations/versions/does-not-exist").status_code == 404
    assert client.get("/api/formulations/versions/a/diff/b").status_code == 404
    assert client.get("/api/formulations/versions/detail/nope").status_code == 404


# ── lineage lookup by name ───────────────────────────────────────────────────


def test_find_lineages_matches_the_root_revision(store):
    """Matching on v1 keeps a lineage findable after a later revision renames
    it — otherwise history would disappear the moment someone retitled."""
    root = store.save_version(_form(name="Primer A"))
    store.save_version(_form(name="Primer A v2"), parent_version_id=root.id)

    assert store.find_lineages(name="Primer A") == [root.lineage_id]
    assert store.find_lineages(name="Primer A v2") == []


def test_find_lineages_filters_by_domain(store):
    store.save_version(_form(name="Shared"))
    assert store.find_lineages(name="Shared", domain="anticorrosion_coating")
    assert store.find_lineages(name="Shared", domain="degreaser") == []


def test_find_lineages_unknown_name_is_empty(store):
    assert store.find_lineages(name="nope") == []


def test_lineage_lookup_endpoint(store):
    client.post(
        "/api/formulations/versions",
        json={"formulation": _form(name="Findable").model_dump(mode="json")},
    )
    response = client.get("/api/formulations/versions?name=Findable")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert [v["version"] for v in body[0]["versions"]] == [1]


def test_lineage_lookup_returns_empty_list_not_404(store):
    """An unmatched name means "no history yet", which the UI renders as a
    prompt to save one — a 404 would read as an error instead."""
    response = client.get("/api/formulations/versions?name=does-not-exist")
    assert response.status_code == 200
    assert response.json() == []
