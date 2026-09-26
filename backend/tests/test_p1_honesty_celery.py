"""P1 wave: Optuna honesty, Celery resilience knobs, virtual-loop labeling."""
from __future__ import annotations

from app.domain.schemas import OptimizationResult, ProductDomain, Requirement
from app.services import dependencies as deps
from app.worker import celery_app as celery_mod


def test_optuna_dependency_label_is_tpe_not_nsga():
    optuna = next(d for d in deps.CATALOG if d.pip_name == "optuna")
    assert "TPE" in optuna.enables
    assert "非 NSGA" in optuna.enables or "标量" in optuna.enables
    # Must not claim to *be* NSGA-II multi-objective.
    assert "NSGA-II 多目标" not in optuna.enables


def test_botorch_dependency_label_mentions_scalar_not_pareto():
    botorch = next(d for d in deps.CATALOG if d.pip_name == "botorch")
    assert "标量" in botorch.enables or "LogEI" in botorch.enables
    assert "Pareto" in botorch.enables or "BayBE" in botorch.enables


def test_celery_acks_late_and_prefetch():
    conf = celery_mod.celery_app.conf
    assert conf.task_acks_late is True
    assert int(conf.worker_prefetch_multiplier) == 1


def test_doe_cycle_task_has_retry_config():
    from app.worker.tasks import run_doe_cycle_task

    assert run_doe_cycle_task.max_retries == 3
    # autoretry_for wired for common transient errors
    autoretry = getattr(run_doe_cycle_task, "autoretry_for", ()) or ()
    assert TimeoutError in autoretry or ConnectionError in autoretry


def test_optimization_result_defaults_virtual_measurement_source():
    res = OptimizationResult(
        iterations=1,
        objective="score",
        history=[0.1],
        top_formulations=[],
    )
    assert res.measurement_source == "predictor_virtual"


def test_run_optimization_labels_virtual_source(monkeypatch):
    from app.pipeline import workflow

    monkeypatch.setattr(
        "app.services.engines.doe_registry.baybe_available",
        lambda: False,
    )
    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    out = workflow.run_optimization(req, iterations=2, engine="legacy")
    assert out.measurement_source == "predictor_virtual"
    assert out.engine  # non-empty
