"""B-3: KB 覆盖率计数持久化到 SQLite。

验证：
1. _bump_kb_coverage 写入 DB，get_kb_coverage_stats 从 DB 读；
2. 模拟进程重启（进程本地 dict 清零）后计数不丢；
3. DB 不可用时 fail-open 回退到进程本地计数。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_index


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    import app.db.database as db_mod

    engine = make_engine(f"sqlite:///{tmp_path}/cov.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    kb_index.reset_kb_coverage_stats()
    yield factory
    kb_index.reset_kb_coverage_stats()


def test_bump_persists_to_db(tmpdb):
    kb_index._bump_kb_coverage(embedded=7, total=10)
    kb_index._bump_kb_coverage(embedded=3, total=5)
    cov = kb_index.get_kb_coverage_stats()
    assert cov["kb_chunks_embedded"] == 10
    assert cov["kb_chunks_total"] == 15


def test_counts_survive_process_restart(tmpdb):
    kb_index._bump_kb_coverage(embedded=7, total=10)
    # 模拟进程重启：进程本地 dict 丢失
    with kb_index._KB_COVERAGE_LOCK:
        kb_index._KB_COVERAGE["kb_chunks_embedded"] = 0
        kb_index._KB_COVERAGE["kb_chunks_total"] = 0
    cov = kb_index.get_kb_coverage_stats()
    assert cov["kb_chunks_embedded"] == 7
    assert cov["kb_chunks_total"] == 10


def test_db_unavailable_falls_back_to_process_local(tmpdb, monkeypatch):
    import app.db.database as db_mod

    kb_index._bump_kb_coverage(embedded=2, total=3)

    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(db_mod, "default_session_factory", _boom)
    cov = kb_index.get_kb_coverage_stats()
    # fail-open：回退到进程本地计数（bump 时同步累加过）
    assert cov["kb_chunks_embedded"] == 2
    assert cov["kb_chunks_total"] == 3
