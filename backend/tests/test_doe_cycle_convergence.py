"""P2-4: target-achieved convergence + budget hard-stop for DOE cycles."""
import pytest

from app.domain.schemas import (
    ExperimentRecord,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.services import doe_cycle_service
from app.services.auto_loop import target_achieved


def _req(target=None, direction="maximize", metric="salt_spray_hours"):
    objectives = []
    if target is not None:
        objectives = [
            ObjectiveSpec(metric=metric, direction=direction, target_value=target)
        ]
    return Requirement(
        project_id="p1",
        domain=ProductDomain.anticorrosion_coating,
        objectives=objectives,
    )


def _rec(measured):
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating, factors={}, measured=measured
    )


# ── target_achieved ──────────────────────────────────────────────


def test_target_achieved_maximize():
    obj = ObjectiveSpec(metric="salt_spray_hours", direction="maximize",
                        target_value=1000.0)
    assert target_achieved(1000.0, obj) is True
    assert target_achieved(1200.0, obj) is True
    assert target_achieved(999.9, obj) is False


def test_target_achieved_minimize():
    obj = ObjectiveSpec(metric="cure_temperature_c", direction="minimize",
                        target_value=80.0)
    assert target_achieved(80.0, obj) is True
    assert target_achieved(70.0, obj) is True
    assert target_achieved(80.1, obj) is False


def test_target_achieved_missing_data_fail_open():
    obj = ObjectiveSpec(metric="salt_spray_hours", direction="maximize",
                        target_value=1000.0)
    assert target_achieved(None, obj) is False
    obj_no_target = ObjectiveSpec(metric="salt_spray_hours", direction="maximize")
    assert target_achieved(5000.0, obj_no_target) is False


# ── run_doe_cycle gates ─────────────────────────────────────────


def _patch_cycle(monkeypatch, records, calls):
    monkeypatch.setattr(
        doe_cycle_service, "load_prior_measurements", lambda req: records
    )
    monkeypatch.setattr(
        doe_cycle_service,
        "build_candidate_formulations",
        lambda req: calls.append("candidates") or [],
    )
    monkeypatch.setattr(
        doe_cycle_service,
        "generate_experiment_dicts",
        lambda req, priors: calls.append("generate") or ("lhs", []),
    )
    recorded = {}

    def _record(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(doe_cycle_service, "record_cycle_run", _record)
    return recorded


def test_budget_exhausted_returns_hold_stub(monkeypatch):
    calls, recorded = [], None
    recorded = _patch_cycle(monkeypatch, [], calls)
    result = doe_cycle_service.run_doe_cycle(_req(), budget_remaining=0)
    assert result["experiment_ids"] == []
    assert result["convergence_reason"] == "budget_exhausted"
    assert result["engine"] == "converged-hold"
    assert "candidates" not in calls and "generate" not in calls
    assert recorded["convergence_reason"] == "budget_exhausted"


def test_budget_negative_returns_hold_stub(monkeypatch):
    calls = []
    recorded = _patch_cycle(monkeypatch, [], calls)
    result = doe_cycle_service.run_doe_cycle(_req(), budget_remaining=-3)
    assert result["convergence_reason"] == "budget_exhausted"
    assert recorded["convergence_reason"] == "budget_exhausted"


def test_target_achieved_returns_hold_stub(monkeypatch):
    calls = []
    records = [_rec({"salt_spray_hours": 1200.0}), _rec({"salt_spray_hours": 800.0})]
    recorded = _patch_cycle(monkeypatch, records, calls)
    result = doe_cycle_service.run_doe_cycle(_req(target=1000.0))
    assert result["experiment_ids"] == []
    assert result["convergence_reason"] == "target_achieved"
    assert result["best_objective_value"] == 1200.0
    assert result["engine"] == "converged-hold"
    assert "generate" not in calls
    assert recorded["convergence_reason"] == "target_achieved"
    assert recorded["best_objective_value"] == 1200.0


def test_target_not_achieved_proceeds(monkeypatch):
    calls = []
    records = [_rec({"salt_spray_hours": 800.0})]
    recorded = _patch_cycle(monkeypatch, records, calls)

    def _persist(req, dicts, cands):
        return {"experiment_ids": ["1"], "status": "success", "count": 1,
                "message": "ok"}

    monkeypatch.setattr(doe_cycle_service, "persist_experiments", _persist)
    # candidates must be non-empty to proceed
    monkeypatch.setattr(
        doe_cycle_service, "build_candidate_formulations",
        lambda req: calls.append("candidates") or ["f1"],
    )
    result = doe_cycle_service.run_doe_cycle(_req(target=1000.0))
    assert "generate" in calls
    assert result.get("convergence_reason") is None
    assert recorded["status"] == "success"
