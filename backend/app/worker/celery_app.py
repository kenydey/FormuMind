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

try:
    # Restore UI-saved settings (secrets / LLM runtime / feature flags) before
    # the worker caches Settings, mirroring the API's lifespan bootstrap.
    from ..services.secrets_store import apply_persisted_ui_settings

    apply_persisted_ui_settings()
except Exception:  # pragma: no cover - never block worker boot on this
    pass

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
    task_track_started=True,
    # Was 600 / 900 (10 / 15 min), which killed every large knowledge-base
    # build. The rest of the stack had already been told these runs may take as
    # long as they take — no client wall-clock limit, a 6 h SSE deadline,
    # kb_ingest_max_docs=0 — so the executor was the one layer still enforcing
    # a limit nobody else believed in, and the UI sat watching a task Celery had
    # already killed. See config.celery_soft_time_limit_s for the ordering rule.
    task_soft_time_limit=settings.celery_soft_time_limit_s,
    task_time_limit=settings.celery_hard_time_limit_s,
    result_expires=86400,
)

# Register Celery tasks on import.
import app.worker.tasks  # noqa: F401
