"""Celery application (legacy / optional).

The API executes async jobs via ``TaskManager`` in ``tasks.py``; Celery is only
needed when running the optional ``worker`` service (``--profile celery``).

Configured to use Redis as broker/result backend when reachable. The
``celery_eager`` setting (default True) makes Celery tasks run synchronously
in-process so tests and CI work without a running worker or broker.
"""
from __future__ import annotations

from celery import Celery

from ..config import get_settings

settings = get_settings()

celery_app = Celery(
    "formumind",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_always_eager=settings.celery_eager,
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)
