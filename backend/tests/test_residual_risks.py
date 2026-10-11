"""残留风险收尾测试：结构检索增强 + beat 调度。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_structure_augment_no_smiles(monkeypatch):
    """无 SMILES 查询时不触发结构搜索。"""
    from app.services import hybrid_search as hs
    # mock rdkit 不可用
    monkeypatch.setattr(hs, "_augment_with_structure_search",
                        lambda q, r, k: r)
    assert hs._augment_with_structure_search("普通查询", [], 10) == []


def test_beat_schedule_has_quality_task():
    """beat 调度包含 chunk 质量抽检。"""
    from app.worker.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    assert "chunk-quality-daily" in schedule
    entry = schedule["chunk-quality-daily"]
    assert entry["task"] == "formumind.chunk_quality_sample"


def test_structure_search_empty():
    """空库时结构搜索返回空。"""
    from app.services import structure_index as si
    # 无数据时应返回 []
    r = si.search_similar_structures("CCO", threshold=0.99, limit=5)
    assert isinstance(r, list)
