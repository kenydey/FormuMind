"""A-8: scripts/doe_simulation.py 进 pytest。

把原本只能手动跑的 CLI 脚本固化为回归测试：确定性（--seed 两次输出一致）、
evaluator 诚实性（synthetic 真值函数可达峰、历史单调）、acquisition 可观测性
（probe 记录每轮实际使用的 engine）。

bayesian 臂在 pytest 中走 legacy 路径（monkeypatch baybe_available→False）：
既快（BayBE 真尝试在沙箱约 85s/iter），又覆盖脚本注释里承诺的
"BayBE 不可用时 legacy LHS+EI" 回退语义。BayBE 真路径由
test_golden_baybe_opt.py 覆盖。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import doe_simulation as sim  # noqa: E402

from app.domain.schemas import ProductDomain, Requirement  # noqa: E402

TARGET = 95.0
METRIC = "salt_spray_hours"


def _req() -> Requirement:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating, project_id="sim-test"
    )


def test_synthetic_evaluator_peak_reachable():
    """诚实性：真值函数在 centers 处取峰 1.05*target，目标可达。"""
    from app.pipeline.workflow import build_doe_factors

    req = _req()
    factors = build_doe_factors(req)
    names, centers, spans = sim._synthetic_calibration(factors, TARGET)
    peak = sim.synthetic_evaluate(centers, names, centers, spans, TARGET)
    assert peak == pytest.approx(1.05 * TARGET)
    # 偏离中心值严格下降
    off = dict(centers)
    off[names[0]] = centers[names[0]] + spans[names[0]]
    assert sim.synthetic_evaluate(off, names, centers, spans, TARGET) < peak


def test_traditional_deterministic():
    """--seed 两次运行：传统臂输出字节级一致。"""
    sim._seed_all(42)
    c1, h1 = sim.simulate_traditional_doe(
        _req(), METRIC, TARGET, batch_size=5, max_batches=2, seed=42
    )
    sim._seed_all(42)
    c2, h2 = sim.simulate_traditional_doe(
        _req(), METRIC, TARGET, batch_size=5, max_batches=2, seed=42
    )
    assert (c1, h1) == (c2, h2)
    # 历史单调不减（best_value 语义）
    assert all(b >= a for a, b in zip(h1, h1[1:]))


def test_traditional_probe_observability():
    """A-8 observability：probe 记录每批 engine/n_suggested/best。"""
    probe: list = []
    sim._seed_all(42)
    count, history = sim.simulate_traditional_doe(
        _req(), METRIC, TARGET, batch_size=5, max_batches=2, seed=42, probe=probe
    )
    assert probe, "expected probe entries"
    assert len(probe) == len(history)
    for entry in probe:
        assert entry["engine"] == "lhs"
        assert entry["n_suggested"] == 5
        assert entry["best"] >= 0
    assert probe[-1]["best"] == pytest.approx(history[-1])


@pytest.fixture()
def _legacy_only(monkeypatch):
    """强制走 legacy 路径：快且确定（跳过 BayBE 慢尝试）。"""
    import app.services.engines.doe_registry as reg

    monkeypatch.setattr(reg, "baybe_available", lambda: False)


def test_bayesian_legacy_deterministic(_legacy_only):
    """bayesian 臂（legacy 回退）：--seed 两次输出一致。"""
    sim._seed_all(42)
    c1, h1 = sim.simulate_bayesian_closed_loop(
        _req(), METRIC, TARGET, max_iterations=2, seed=42
    )
    sim._seed_all(42)
    c2, h2 = sim.simulate_bayesian_closed_loop(
        _req(), METRIC, TARGET, max_iterations=2, seed=42
    )
    assert (c1, h1) == (c2, h2)
    assert all(b >= a for a, b in zip(h1, h1[1:]))


def test_bayesian_probe_reports_engine(_legacy_only):
    """A-8 observability：probe 透出每轮实际 acquisition engine。"""
    probe: list = []
    sim._seed_all(42)
    count, history = sim.simulate_bayesian_closed_loop(
        _req(), METRIC, TARGET, max_iterations=2, seed=42, probe=probe
    )
    assert probe, "expected probe entries"
    assert len(probe) == len(history)
    for entry in probe:
        assert entry["engine"] == "legacy"  # baybe_available 已 mock 为 False
        assert entry["n_suggested"] > 0
        assert entry["best"] == pytest.approx(history[entry["iteration"] - 1])
