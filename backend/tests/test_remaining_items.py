"""剩余项目测试：S-2/P-3/迁移/O-1。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_threshold_adapt_defaults():
    """S-2: 无数据时返回默认阈值。"""
    from app.services.threshold_adapt import adapt_thresholds, DEFAULT_THRESHOLDS
    t = adapt_thresholds()
    assert t == DEFAULT_THRESHOLDS


def test_page_calibrate():
    """P-3: 页码校准。"""
    from app.services.page_calibrate import calibrate_page_numbers
    approx = [5, 10, 15]
    bookmarks = [
        {"title": "第一章", "page": 5},
        {"title": "第二章", "page": 11},
    ]
    calibrated = calibrate_page_numbers(approx, bookmarks, 20)
    assert calibrated[0] == 5  # 命中书签
    assert calibrated[1] == 11  # 校准到书签
    # 无书签时返回原值
    assert calibrate_page_numbers(approx, [], 20) == approx


def test_transfer_learn():
    """迁移学习。"""
    from app.services.transfer_learn import find_similar_history, warm_start_points
    history = [
        {"objectives": ["hardness", "adhesion"], "best_point": [0.5, 0.3]},
        {"objectives": ["gloss"], "best_point": [0.8]},
    ]
    req = {"objectives": ["hardness", "adhesion", "gloss"]}
    similar = find_similar_history(req, history, top_k=2)
    assert len(similar) == 2
    # 维度匹配
    class FakeFactor:
        pass
    points = warm_start_points(similar, [FakeFactor(), FakeFactor()])
    assert len(points) == 1  # 只有第一个维度匹配


def test_ab_tracking():
    """O-1: A/B 追踪。"""
    from app.services.recommend_ab_track import get_ab_stats
    stats = get_ab_stats(days=14)
    assert "sufficient_data" in stats
    # 无数据时
    assert stats["sufficient_data"] is False


def test_kb_incremental():
    """KB 增量更新。"""
    from app.services.kb_incremental import content_hash
    h1 = content_hash(b"hello")
    h2 = content_hash(b"hello")
    h3 = content_hash(b"world")
    assert h1 == h2
    assert h1 != h3
