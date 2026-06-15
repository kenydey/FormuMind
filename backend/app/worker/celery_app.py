"""Celery application.

Configured to use Redis as broker/result backend when reachable. The
``celery_eager`` setting (default True) makes tasks run synchronously in-process
so the API works without a running worker or broker — ideal for development,
CI, and the offline MVP.
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
