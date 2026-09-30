"""B-5: rigor_gate 对抗硬门禁的 mutation 验证。

把 ``value_correctness`` 指标变异为"永远打 1.0"（模拟指标退化、对数值
trap 全部漏网），门禁必须 exit 1。证明对抗硬门禁是真实生效的判定，
而非空转的摆设。
"""
from __future__ import annotations

import importlib.util
import os

import pytest


def _muted_value_correctness(answer, pair_meta):
    # 变异：指标失明，对数值 trap 永远判"通过"（= 漏网 MISSED）。
    return {"score": 1.0, "failures": []}


def _load_rigor_gate():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts",
        "rigor_gate.py",
    )
    spec = importlib.util.spec_from_file_location("rigor_gate_b5", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    os.environ.get("FORMUMIND_RIGOR_GATE", "").strip().lower()
    in ("0", "false", "no", "off"),
    reason="rigor gate disabled by env",
)
def test_b5_mutation_numeric_trap_miss_exits_1(monkeypatch, capsys):
    import app.evals.rigor_rubric as rr

    monkeypatch.setattr(rr, "metric_value_correctness", _muted_value_correctness)
    gate = _load_rigor_gate()
    rc = gate.main()
    out = capsys.readouterr().out
    assert rc == 1, f"muted value_correctness must fail the hard gate, rc={rc}"
    assert "ADVERSARIAL GATE FAILED" in out
    assert "value_correctness" in out


@pytest.mark.skipif(
    os.environ.get("FORMUMIND_RIGOR_GATE", "").strip().lower()
    in ("0", "false", "no", "off"),
    reason="rigor gate disabled by env",
)
def test_b5_gate_passes_without_mutation(capsys):
    gate = _load_rigor_gate()
    rc = gate.main()
    out = capsys.readouterr().out
    assert rc == 0, f"unmutated gate must pass, rc={rc}\n{out[-2000:]}"
