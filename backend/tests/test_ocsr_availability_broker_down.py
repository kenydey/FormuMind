"""OCSR availability must not spend seconds in broker retries when the broker is down (round-4).

``GET /api/settings/ocsr`` (polled by the settings panel) pinged the Celery workers unconditionally; with Redis
unreachable ``control.ping`` sat in kombu's connection retries for 6–8 s before answering "not installed".
"""
from __future__ import annotations

import time

from app.services import ocsr


def test_a_down_broker_answers_immediately_without_pinging(monkeypatch):
    import app.api._dispatch as dispatch
    import app.worker.celery_app as celery_module

    monkeypatch.setattr(dispatch, "broker_reachable", lambda: False)

    def boom(*args, **kwargs):
        raise AssertionError("must not ping workers through a broker that is down")

    monkeypatch.setattr(celery_module.celery_app.control, "ping", boom)
    monkeypatch.setitem(ocsr._alive_cache, "t", 0.0)
    started = time.monotonic()
    assert ocsr._molscribe_worker_alive() is False
    assert time.monotonic() - started < 1.0


def test_a_reachable_broker_still_looks_for_the_molscribe_worker(monkeypatch):
    import app.api._dispatch as dispatch
    import app.worker.celery_app as celery_module

    monkeypatch.setattr(dispatch, "broker_reachable", lambda: True)
    monkeypatch.setattr(celery_module.celery_app.control, "ping", lambda timeout=2: {"molscribe@host": {"ok": "pong"}})
    monkeypatch.setitem(ocsr._alive_cache, "t", 0.0)
    assert ocsr._molscribe_worker_alive() is True
