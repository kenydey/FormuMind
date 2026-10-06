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
    campaign.add_measurements(records_to_dataframe(records, req, req.objectives, include_sources={"golden"}))
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
            records_to_dataframe(new_records, req, req.objectives, include_sources={"golden"})
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


class _MockHillClimbCampaign:
    """B-6: deterministic hill-climbing stand-in for ``baybe.Campaign``.

    Runs in CI where the ``baybe`` extra (torch) is absent. It verifies the
    *integration* rather than BayBE itself: ``records_to_dataframe`` column
    alignment, ``recommend`` → ``dataframe_to_doe_plan`` conversion, and the
    round/batch loop plumbing. A broken measurement framing (misaligned
    columns) or plan conversion collapses even this simple optimizer to
    random — the same failure mode the real BayBE golden guards against.

    The strategy (perturb around the best-seen point) is verified to beat
    seeded random on the synthetic quadratic across multiple seeds; it is
    deliberately weaker than BayBE, so this test is a wiring gate, not a
    BayBE quality claim.
    """

    def __init__(self, factor_list, rng, step: float = 0.08):
        self._factors = factor_list
        self._rng = rng
        self._step = step
        self._best: dict | None = None
        self._best_score = float("-inf")

    def add_measurements(self, df) -> None:
        for _, row in df.iterrows():
            score = float(row["synth_score"])
            if score > self._best_score:
                self._best_score = score
                self._best = {
                    f.name: float(row[f.name]) for f in self._factors
                }

    def recommend(self, batch_size: int):
        import pandas as pd

        assert self._best is not None, "recommend() before add_measurements()"
        rows = []
        for _ in range(batch_size):
            point = {}
            for f in self._factors:
                span = (f.high - f.low) or 1.0
                v = self._best[f.name] + self._rng.normal(0.0, self._step * span)
                point[f.name] = float(min(max(v, f.low), f.high))
            rows.append(point)
        return pd.DataFrame(rows)


def test_golden_mock_optimizer_beats_random_on_synthetic():
    """B-6: without baybe installed, the mock arm must still beat random.

    Exercises the real adapters (records_to_dataframe /
    dataframe_to_doe_plan) and loop plumbing end to end.
    """
    # NOTE: import from the adapter modules directly — importing
    # app.services.engines.baybe_engine would pull the baybe/torch chain.
    from app.services.engines.adapters.baybe_space_builder import (
        factors_for_requirement,
    )
    from app.services.engines.adapters.doe_adapter import dataframe_to_doe_plan
    from app.services.engines.adapters.measurements_adapter import (
        records_to_dataframe,
    )

    rng = np.random.default_rng(7)
    req = _req()
    factor_list = factors_for_requirement(req, None)
    assert factor_list, "no factors for requirement"
    truth = _truth_factory(factor_list)
    seed_rows = _seed_rows(factor_list, rng)

    # ── Mock optimizer arm (same budget/shape as the BayBE golden) ──
    records = _to_records(req, seed_rows, truth)
    campaign = _MockHillClimbCampaign(factor_list, np.random.default_rng(8))
    campaign.add_measurements(records_to_dataframe(records, req, req.objectives, include_sources={"golden"}))
    mock_best = max(r.measured["synth_score"] for r in records)
    for _ in range(N_ROUNDS):
        rec_df = campaign.recommend(batch_size=BATCH)
        plan = dataframe_to_doe_plan(
            rec_df, factor_list, "mock_active", engine="mock"
        )
        new_rows = [dict(r.natural) for r in plan.runs]
        assert new_rows, "mock campaign suggested no points"
        # 集成断言：plan 必须携带全部因子的自然值（转换链不断裂）
        assert all(
            set(r.keys()) == {f.name for f in factor_list} for r in new_rows
        )
        new_records = _to_records(req, new_rows, truth)
        records.extend(new_records)
        campaign.add_measurements(
            records_to_dataframe(new_records, req, req.objectives, include_sources={"golden"})
        )
        mock_best = max(
            [mock_best] + [truth(r)["synth_score"] for r in new_rows]
        )

    # ── Random baseline arm (same budget) ──
    rng2 = np.random.default_rng(7)
    random_best = max(truth(r)["synth_score"] for r in seed_rows)
    for _ in range(N_ROUNDS):
        for _ in range(BATCH):
            r = {f.name: float(rng2.uniform(f.low, f.high)) for f in factor_list}
            random_best = max(random_best, truth(r)["synth_score"])

    assert mock_best > random_best, (
        f"mock optimizer ({mock_best:.1f}) did not beat random ({random_best:.1f}); "
        "measurement framing or plan conversion may have regressed"
    )


def test_records_to_dataframe_filters_virtual_by_default():
    """P0-5：GP 训练数据默认只收 lab 真实测量，虚拟记录不进 GP。"""
    from app.services.engines.adapters.measurements_adapter import records_to_dataframe

    req = _req()
    recs = [
        ExperimentRecord(domain=req.domain, factors={"x": 1.0},
                         measured={"synth_score": 10.0}, source="lab"),
        ExperimentRecord(domain=req.domain, factors={"x": 2.0},
                         measured={"synth_score": 20.0}, source="lab"),
        ExperimentRecord(domain=req.domain, factors={"x": 3.0},
                         measured={"synth_score": 999.0}, source="baybe_opt"),
        ExperimentRecord(domain=req.domain, factors={"x": 4.0},
                         measured={"synth_score": 888.0}, source="predictor_virtual"),
    ]
    df = records_to_dataframe(recs, req, req.objectives)
    # 只有 2 条 lab 进 GP；虚拟的 999/888 不能污染训练数据
    assert len(df) == 2, f"应只含 lab 记录，实际 {len(df)} 行"
    assert sorted(df["synth_score"].tolist()) == [10.0, 20.0]

    # 显式 opt-in 才放行虚拟（冷启动等专用路径）
    df_all = records_to_dataframe(
        recs, req, req.objectives,
        include_sources={"lab", "baybe_opt", "predictor_virtual"},
    )
    assert len(df_all) == 4
