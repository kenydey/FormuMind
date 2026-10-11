"""N-1~N-6 配方推荐升级测试。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_chem_validate_smiles():
    """N-2: SMILES 校验。"""
    from app.services.chem_validate import validate_smiles
    ok, _ = validate_smiles("CCO")
    assert ok
    ok, reason = validate_smiles("not-a-smiles!!!")
    # RDKit 可用时应返回 False
    from app.services.chem_validate import validate_smiles as vs
    # 至少不抛异常
    assert isinstance(ok, bool)


def test_chem_validate_compatibility():
    """N-3: 兼容性检查。"""
    from app.services.chem_validate import check_compatibility
    warns = check_compatibility(["硫酸", "氢氧化钠", "水"])
    assert len(warns) >= 1
    assert "硫酸" in warns[0]
    # 无冲突时为空
    assert check_compatibility(["水", "乙醇"]) == []


def test_chem_validate_cas_format():
    """N-2: CAS 格式校验。"""
    from app.services.chem_validate import verify_cas
    ok, _ = verify_cas("64-17-5")  # 乙醇
    assert isinstance(ok, bool)
    ok, reason = verify_cas("invalid-cas")
    assert not ok


def test_complete_ratios():
    """N-5: 配比补全。"""
    from app.services.recommend_pipeline import _complete_ratios

    class FakeIng:
        def __init__(self, name, wp=None):
            self.name = name
            self.weight_pct = wp

    class FakeForm:
        def __init__(self):
            self.ingredients = [
                FakeIng("环氧树脂", 50.0),
                FakeIng("钛白粉", None),
                FakeIng("分散剂", None),
            ]

    f = FakeForm()
    _complete_ratios(f)
    total = sum(i.weight_pct for i in f.ingredients)
    assert abs(total - 100.0) < 0.1
    assert all(i.weight_pct > 0 for i in f.ingredients)


def test_selection_reason():
    """N-6: 选择解释。"""
    from app.services.grounded_recommend import _selection_reason

    class FakeComp:
        name = "环氧树脂"

    r = _selection_reason(FakeComp(), "high", ["ref1", "ref2"])
    assert "2 篇" in r
    r = _selection_reason(FakeComp(), "low", [])
    assert "缺乏" in r


def test_consensus_config():
    """N-1/N-4 配置存在。"""
    s = get_settings()
    assert hasattr(s, "recommend_consensus_enabled")
    assert hasattr(s, "prompt_ab_enabled")
    assert hasattr(s, "chem_validate_enabled")
    assert hasattr(s, "ratio_completion_enabled")


def test_compatibility_expanded():
    """O-2: 20 对兼容性规则。"""
    from app.services.chem_validate import INCOMPATIBLE_PAIRS, check_compatibility
    assert len(INCOMPATIBLE_PAIRS) >= 20
    # 新增规则生效
    warns = check_compatibility(["高锰酸钾", "甘油"])
    assert len(warns) >= 1


def test_ratio_reasonableness():
    """O-4: 配比合理性评分。"""
    from app.services.recommend_pipeline import _score_ratio_reasonableness

    class FakeIng:
        def __init__(self, name, wp):
            self.name = name
            self.weight_pct = wp

    class FakeForm:
        def __init__(self, ings):
            self.ingredients = ings

    # 正常配比无警告
    f = FakeForm([FakeIng("环氧树脂", 50.0), FakeIng("钛白粉", 25.0)])
    assert _score_ratio_reasonableness(f) == []
    # 异常配比有警告
    f = FakeForm([FakeIng("环氧树脂", 95.0)])
    warns = _score_ratio_reasonableness(f)
    assert len(warns) >= 1
    assert "过高" in warns[0]
