"""P0-1: 全文下载重试队列测试。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_enqueue_retry_returns_none_in_eager(monkeypatch):
    """eager 模式下不入队列（fail-open）。"""
    from app.worker.tasks import enqueue_fulltext_retry
    monkeypatch.setattr(get_settings(), "celery_eager", True, raising=False)
    assert enqueue_fulltext_retry("US123", "USPTO", "patent") is None


def test_enqueue_retry_fail_open(monkeypatch):
    """Celery 不可用时 fail-open 返回 None。"""
    from app.worker import tasks
    monkeypatch.setattr(get_settings(), "celery_eager", False, raising=False)
    # mock delay 抛异常
    monkeypatch.setattr(tasks.retry_fulltext_fetch, "delay",
                        lambda payload: (_ for _ in ()).throw(RuntimeError("no broker")))
    assert tasks.enqueue_fulltext_retry("US123", "USPTO", "patent") is None


def test_retry_task_persists_on_success(monkeypatch):
    """重试成功后走正常 persist 流程。"""
    from app.services.pdf_downloader import FetchedFulltext
    from app.worker.tasks import retry_fulltext_fetch
    from app.services import fulltext_fetcher as ff

    monkeypatch.setattr(
        ff, "_dispatch_fetch",
        lambda kind, ev, timeout, allow_pdf: FetchedFulltext(
            text="retry fetched text " * 50, pdf_bytes=b"%PDF", tables=[],
        ),
    )
    monkeypatch.setattr(ff, "_last_acquisition", lambda: "pdf")
    persisted = {}
    monkeypatch.setattr(
        ff, "_persist_fulltext",
        lambda text, ev, kind, **kw: persisted.update(
            text=text, pdf_bytes=kw.get("pdf_bytes")) or "src-retry",
    )

    # 构造 fake self（bind=True 的 task）
    class FakeSelf:
        class request:
            retries = 0
            id = "test-id"
        def retry(self, exc=None, countdown=None):
            raise RuntimeError("should not retry on success")

    # bind=True task：通过 _run_test 直接调底层函数
    from celery import Task
    func = retry_fulltext_fetch.__wrapped__ if hasattr(
        retry_fulltext_fetch, "__wrapped__") else None
    # Celery Task 对象：用 run 方法，self 通过 _task_stack 模拟
    # 简化：直接调用被装饰的原始函数
    import types
    raw = retry_fulltext_fetch.run.__func__ if isinstance(
        retry_fulltext_fetch.run, types.MethodType) else retry_fulltext_fetch.run
    result = raw(
        FakeSelf(),
        {"identifier": "US123", "source": "USPTO", "kind": "patent",
         "project_id": None, "title": "t", "url": None},
    )
    assert result["ok"] is True
    assert result["source_id"] == "src-retry"
    assert persisted["pdf_bytes"] == b"%PDF"
    assert "retry fetched text" in persisted["text"]
