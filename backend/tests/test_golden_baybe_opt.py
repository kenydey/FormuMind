"""P1-3 golden: BayBE optimizer quality gate.

A deterministic synthetic benchmark: a fixed quadratic truth function is
optimized through OUR BayBE integration (measurement framing via
``records_to_dataframe`` + ``campaign.recommend`` + ``dataframe_to_doe_plan``).
BayBE must beat a seeded random baseline — this is what guards against
regressions in the integration (broken measurement alignment, bad campaign
seeding, wrong plan conversion all collapse the optimizer to random).

Why not reuse scripts/doe_simulation.py directly: that script's
"improvement" was ``random.uniform(0, headroom*0.4)``, independent of the
suggested points — converting it to a pytest would have tested the RNG,
not the optimizer. The script now defaults to the same synthetic evaluator
used here.

The benchmark needs the ``baybe`` extra (torch); CI's backend job does not
install it, so the benchmark skips there. The no-baybe fallback path is
covered by ``test_golden_baybe_unavailable_falls_back``, which runs in CI.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.domain.schemas import ExperimentRecord, ObjectiveSpec, ProductDomain, Requirement
from app.services.engines.doe_registry import baybe_available

OPTIMUM = 1000.0
N_SEED = 8
N_ROUNDS = 4
BATCH = 4


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="synth_score", weight=1.0, direction="maximize")],
    )


def _truth_factory(factor_list):
    names = [f.name for f in factor_list]
    centers = {f.name: f.low + 0.75 * (f.high - f.low) for f in factor_list}
    spans = {f.name: (f.high - f.low) or 1.0 for f in factor_list}

    def truth(natural: dict) -> dict:
        return {
            "synth_score": OPTIMUM
            - 600.0 * sum(((natural[n] - centers[n]) / spans[n]) ** 2 for n in names)
        }

    return truth


def _seed_rows(factor_list, rng):
    return [
        {f.name: float(rng.uniform(f.low, f.high)) for f in factor_list}
        for _ in range(N_SEED)
    ]


def _to_records(req, rows, truth):
    return [
        ExperimentRecord(
            domain=req.domain, factors=dict(r), measured=truth(r), source="golden"
        )
        for r in rows
    ]


@pytest.mark.skipif(not baybe_available(), reason="baybe extra not installed")
def test_golden_baybe_beats_random_on_synthetic(monkeypatch):
    """BayBE loop must beat seeded random search on the synthetic quadratic."""
    import torch

    from app.services.engines.baybe_engine import (
        BaybeCampaignEngine,
        dataframe_to_doe_plan,
        records_to_dataframe,
    )

    monkeypatch.setenv("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
    torch.manual_seed(7)
    rng = np.random.default_rng(7)

    req = _req()
    eng = BaybeCampaignEngine()
    campaign, factor_list = eng._new_campaign(req, req.objectives)
    truth = _truth_factory(factor_list)
    seed_rows = _seed_rows(factor_list, rng)

    # ── BayBE arm ──
    records = _to_records(req, seed_rows, truth)
    campaign.add_measurements(records_to_dataframe(records, req, req.objectives))
    baybe_best = max(r.measured["synth_score"] for r in records)
    for _ in range(N_ROUNDS):
        rec_df = campaign.recommend(batch_size=BATCH)
        plan = dataframe_to_doe_plan(
            rec_df, factor_list, "baybe_active", engine="baybe"
        )
        new_rows = [dict(r.natural) for r in plan.runs]
        assert new_rows, "baybe suggested no points"
        new_records = _to_records(req, new_rows, truth)
        records.extend(new_records)
        campaign.add_measurements(
            records_to_dataframe(new_records, req, req.objectives)
        )
        baybe_best = max(
            [baybe_best] + [truth(r)["synth_score"] for r in new_rows]
        )

    # ── Random baseline arm (same budget) ──
    rng2 = np.random.default_rng(7)
    random_best = max(truth(r)["synth_score"] for r in seed_rows)
    for _ in range(N_ROUNDS):
        for _ in range(BATCH):
            r = {f.name: float(rng2.uniform(f.low, f.high)) for f in factor_list}
            random_best = max(random_best, truth(r)["synth_score"])

    assert baybe_best > random_best, (
        f"BayBE ({baybe_best:.1f}) did not beat random ({random_best:.1f}); "
        "optimizer integration may have regressed"
    )


def test_golden_baybe_unavailable_falls_back(monkeypatch):
    """No baybe installed (CI): engine='auto' must degrade to legacy, not crash."""
    import app.services.engines.doe_registry as reg_mod
    from app.services.active_learning import active_learning_doe

    monkeypatch.setattr(reg_mod, "baybe_available", lambda: False)
    req = _req()
    res = active_learning_doe(req, existing=[], n_suggest=2, engine="auto")
    assert res.engine == "legacy"
    assert len(res.plan.runs) >= 1
