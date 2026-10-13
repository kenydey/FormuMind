"""9.5 升级测试：公式/元数据/早停/表格/DOE/帕累托。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_extract_latex_formulas():
    """公式识别。"""
    from app.services.parsing import extract_latex_formulas
    md = "文本 $$E=mc^2$$ 更多 \\[x^2 + y^2\\] 结束"
    formulas = extract_latex_formulas(md)
    assert len(formulas) == 2
    assert "E=mc^2" in formulas[0][0]


def test_auto_tag_chunk():
    """元数据自动标签。"""
    from app.services.kb_enrich import auto_tag_chunk
    tags = auto_tag_chunk("环氧树脂配方包含固化剂，测试标准 ASTM")
    assert "配方" in tags
    assert "测试" in tags


def test_optimizer_early_stopping():
    """早停策略。"""
    from app.services.optimizer import BayesianOptimizer
    from app.services.optimizer import Factor
    opt = BayesianOptimizer(
        factors=[Factor(name="x", low=0, high=1)],
        seed=0,
    )
    # 无提升时应早停
    for i in range(10):
        opt.observe([0.5], 1.0)  # 恒定值
    assert opt.should_stop_early(patience=5)
    # 有提升时不应早停
    opt2 = BayesianOptimizer(
        factors=[Factor(name="x", low=0, high=1)],
        seed=0,
    )
    for i in range(10):
        opt2.observe([0.5], float(i))  # 持续提升
    assert not opt2.should_stop_early(patience=5)


def test_adapt_factor_ranges():
    """自适应 DOE。"""
    from app.services.doe_adaptive import adapt_factor_ranges

    class FakeFactor:
        def __init__(self, name, low, high):
            self.name = name
            self.low = low
            self.high = high

    factors = [FakeFactor("temp", 20, 100)]
    # 最优解在上边界
    results = [
        {"temp": 90, "response": 0.9},
        {"temp": 95, "response": 0.95},
        {"temp": 99, "response": 0.99},
    ]
    adapted = adapt_factor_ranges(factors, results)
    assert adapted[0].high > 100  # 范围扩展
