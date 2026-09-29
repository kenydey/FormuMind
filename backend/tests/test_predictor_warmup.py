"""R4: predictor 冷启动预热测试(2026-09-04)。"""
from __future__ import annotations

from app.services import predictor


def test_warm_predict_idempotent(monkeypatch):
    """连续调用只执行一次预热体(_do_warm), 模块级 guard 生效。"""
    calls = {"n": 0}

    def fake_do_warm():
        calls["n"] += 1

    monkeypatch.setattr(predictor, "_warm_guard", {"done": False})
    monkeypatch.setattr(predictor, "_do_warm", fake_do_warm)
    predictor.warm_predict()
    predictor.warm_predict()
    predictor.warm_predict()
    assert calls["n"] == 1
    assert predictor._warm_guard["done"] is True


def test_warm_predict_failure_silent(monkeypatch):
    """预热体抛异常 → warm_predict 吞掉(记日志), 不向调用方传播。"""
    monkeypatch.setattr(predictor, "_warm_guard", {"done": False})

    def boom():
        raise RuntimeError("thermo offline")

    monkeypatch.setattr(predictor, "_do_warm", boom)
    predictor.warm_predict()  # 不应 raise


def test_warm_predict_after_guard_reset_runs_again(monkeypatch):
    """guard 重置后(新进程语义)可再次预热。"""
    calls = {"n": 0}

    def fake_do_warm():
        calls["n"] += 1

    monkeypatch.setattr(predictor, "_do_warm", fake_do_warm)
    monkeypatch.setattr(predictor, "_warm_guard", {"done": False})
    predictor.warm_predict()
    monkeypatch.setattr(predictor, "_warm_guard", {"done": False})
    predictor.warm_predict()
    assert calls["n"] == 2


def test_molecular_features_rdkit_missing_warns_once(monkeypatch, caplog):
    """A-3: RDKit 缺失时不再静默降级 — 记录一次 warning，仍返回 {}。"""
    import logging
    import sys

    from app.domain.schemas import Formulation, Ingredient

    monkeypatch.setattr(predictor, "_RDKIT_MISSING_WARNED", False)
    # 让 `from rdkit import ...` 抛 ImportError（rdkit 本机已装，需屏蔽）。
    monkeypatch.setitem(sys.modules, "rdkit", None)
    monkeypatch.setitem(sys.modules, "rdkit.Chem", None)

    form = Formulation(
        name="t",
        domain="surface_treatment",
        ingredients=[Ingredient(name="水", weight_pct=100.0, smiles="O", role="solvent")],
    )
    with caplog.at_level(logging.WARNING, logger="app.services.predictor"):
        assert predictor._molecular_features(form) == {}
        assert predictor._molecular_features(form) == {}
    warnings = [r for r in caplog.records if "RDKit not installed" in r.message]
    assert len(warnings) == 1, [r.message for r in caplog.records]
    assert predictor._RDKIT_MISSING_WARNED is True
