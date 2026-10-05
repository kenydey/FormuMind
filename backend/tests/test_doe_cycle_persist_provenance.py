"""``persist_experiments`` must not link provenance from inside its own write transaction (round-4).

``provenance.link`` opens its own connection. Called while ``persist_experiments`` still held SQLite's single
write lock it waited behind that very transaction, timed out after the 30 s busy timeout *twice per link*
(``ensure_provenance`` + the insert) and "failed open": one experiment × one candidate took 60 s and recorded
nothing; a real cycle (N runs × up to 5 candidates) stalled for minutes with the writer lock held against every
other request. Found by walking the OpenAPI surface — one request after ``POST /api/doe/cycle`` everything that
wrote hung.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.config import get_settings
from app.db.database import Base, default_engine
from app.domain import knowledge
from app.domain.project_workspace import default_requirement
from app.services import doe_cycle_service as svc
from app.services import provenance


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/prov.db")
    get_settings.cache_clear()
    Base.metadata.create_all(default_engine())
    yield
    get_settings.cache_clear()


def _run(func, timeout: float):
    box: dict = {}

    def target():
        box["value"] = func()

    thread = threading.Thread(target=target, name="eager-persist-provenance-test", daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(timeout)
    return box, thread.is_alive(), time.monotonic() - started


def test_experiments_are_persisted_and_linked_without_waiting_on_their_own_lock(db):
    req = default_requirement()
    candidate = knowledge.baseline_formulation(req)
    experiments = [
        {"run_id": i + 1, "natural_factors": {"Zinc phosphate": 5.0 + i}, "ai_suggested": False, "infeasible": False, "infeasible_reason": ""}
        for i in range(3)
    ]
    box, still_running, elapsed = _run(lambda: svc.persist_experiments(req, experiments, [candidate]), timeout=20)
    assert not still_running, "persist_experiments is waiting on a lock it holds itself"
    assert elapsed < 10
    result = box["value"]
    assert result["count"] == 3 and len(result["experiment_ids"]) == 3

    # lineage(node) lists the edges *pointing at* the node: the runs that tested this candidate
    edges = provenance.lineage("formulation", provenance.formulation_id_for(candidate), depth=1)
    linked_runs = {e["from_id"] for e in edges if e.get("from_type") == "run" and e.get("relation") == "tests"}
    assert linked_runs == set(result["experiment_ids"]), edges


def test_a_provenance_failure_still_does_not_break_the_cycle(db, monkeypatch):
    req = default_requirement()
    monkeypatch.setattr(provenance, "link", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provenance down")))
    experiments = [{"run_id": 1, "natural_factors": {"Zinc phosphate": 5.0}, "ai_suggested": False, "infeasible": False, "infeasible_reason": ""}]
    out = svc.persist_experiments(req, experiments, [knowledge.baseline_formulation(req)])
    assert out["status"] == "success" and out["count"] == 1
