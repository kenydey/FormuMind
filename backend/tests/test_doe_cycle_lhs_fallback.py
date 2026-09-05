"""Regression: DOE cycle LHS fallback must not NameError on dict self-reference."""
from __future__ import annotations

from types import SimpleNamespace


def test_lhs_fallback_reads_infeasible_reason_from_run(monkeypatch):
    """When BayBE is unavailable, LHS path builds exp dicts from ``run`` attrs.

    Pre-fix used ``exp_dict["infeasible_reason"]`` while constructing
    ``exp_dict`` → NameError, caught by the broad except as "Generation failed".
    """
    from app.services import doe_cycle_service as mod

    run = SimpleNamespace(
        run_id="lhs-1",
        coded={"x1": 0.0},
        natural={"resin_wt_pct": 62.0},
        ai_suggested=True,
        infeasible=False,
        infeasible_reason=None,
    )
    active_result = SimpleNamespace(plan=SimpleNamespace(runs=[run]))

    class _UnavailableBaybe:
        def available(self) -> bool:
            return False

    import app.services.engines.baybe_engine as baybe_mod
    import app.services.active_learning as al_mod
    import app.domain.knowledge as knowledge

    monkeypatch.setattr(baybe_mod, "BaybeCampaignEngine", _UnavailableBaybe)
    monkeypatch.setattr(al_mod, "active_learning_doe", lambda **kwargs: active_result)
    monkeypatch.setattr(
        knowledge, "baseline_formulation", lambda req: SimpleNamespace(name="baseline")
    )

    captured: list[dict] = []

    class _ExpRow:
        def __init__(self, **kwargs):
            self.id = "exp-lhs-1"
            captured.append(kwargs)

    class _Session:
        def add(self, obj):
            pass

        def flush(self):
            pass

    class _CM:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(mod, "ExperimentRow", _ExpRow)
    monkeypatch.setattr(mod, "default_session_factory", lambda: object())
    monkeypatch.setattr(mod, "commit_session", lambda factory: _CM())

    requirement = SimpleNamespace(
        domain=SimpleNamespace(value="coating"),
        project_id="proj-1",
    )

    result = mod.run_doe_cycle(requirement)

    assert result["status"] == "success", result
    assert result["count"] == 1
    assert captured, "expected ExperimentRow construction"
    meta = captured[0]["factors"]["_doe_metadata"]
    assert meta["ai_suggested"] is True
    assert meta["infeasible"] is False
    assert meta["infeasible_reason"] == ""
