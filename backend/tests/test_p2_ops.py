"""P2-7/8/9 测试：趋势告警 + 质量抽检 + 断点续传。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestAcquisitionTrend:
    def test_trend_insufficient_data(self):
        from app.services import acquisition_trend as at
        at.clear()
        t = at.trend()
        assert t["trend"] == "stable"
        assert t["alert"] is False

    def test_trend_detects_drop(self):
        from app.services import acquisition_trend as at
        at.clear()
        # 基线：高成功率
        for _ in range(5):
            at._snapshots.append({
                "ts": 1.0, "success_rate": 0.95, "total": 100, "stats": {},
            })
        # 近期：低成功率
        for _ in range(5):
            at._snapshots.append({
                "ts": 2.0, "success_rate": 0.60, "total": 100, "stats": {},
            })
        t = at.trend(window=5)
        assert t["trend"] == "down"
        assert t["alert"] is True
        assert t["drop_pct"] > 15
        at.clear()

    def test_trend_stable(self):
        from app.services import acquisition_trend as at
        at.clear()
        for _ in range(6):
            at._snapshots.append({
                "ts": 1.0, "success_rate": 0.90, "total": 50, "stats": {},
            })
        t = at.trend(window=3)
        assert t["alert"] is False
        at.clear()


class TestChunkQuality:
    def test_sample_empty(self, monkeypatch):
        from app.services import chunk_quality as cq
        # mock 空库
        monkeypatch.setattr(cq, "sample_chunks", lambda n=20: [])
        r = cq.run_quality_sample(n=5)
        assert r["sampled"] == 0

    def test_score_no_llm(self, monkeypatch):
        from app.services import chunk_quality as cq
        monkeypatch.setattr(get_settings(), "deepseek_api_key", "", raising=False)
        monkeypatch.setattr(get_settings(), "openai_api_key", "", raising=False)
        assert cq.score_chunk({"text": "x" * 100}) == {}

    def test_last_report_empty(self):
        from app.services import chunk_quality as cq
        # 不断言具体内容，只验证接口可用
        r = cq.last_report()
        assert isinstance(r, dict)


class TestResumeDownload:
    def test_resume_path(self):
        from app.services import resume_download as rd
        p = rd._resume_path("abc123")
        assert "abc123" in p
        assert p.endswith(".part")

    def test_clear_cache(self):
        from app.services import resume_download as rd
        n = rd.clear_resume_cache(max_age_hours=0)
        assert isinstance(n, int)
