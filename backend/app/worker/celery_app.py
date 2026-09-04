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

# ── OCSR prewarm ───────────────────────────────────────────────────────────
# MolScribe 跑在独立 venv 的 worker 里；`import` 即加载模型（~35s），否则会落在首个
# recognize 任务上超时。仅 molscribe worker（可 import MolScribe）预热；主 worker 不加载。
from celery.signals import worker_process_init


@worker_process_init.connect
def _prewarm_ocsr(**kwargs):  # pragma: no cover - runs in the molscribe worker
    from ..services import ocsr

    if ocsr.molscribe_available():
        ocsr.prewarm_molscribe()


@worker_process_init.connect
def _prewarm_predictor(**kwargs):  # pragma: no cover - runs in worker processes
    # R4 (2026-09-04): DOE/loop 任务跑在 worker, 首个 predict 冷启动实测
    # 9-29s(thermo 数据库初始化 + rdkit)——worker_process_init 在 prefork
    # 子进程启动时触发, 预热前置使任务内的首个 predict 不付数据库初始化。
    # 幂等 + 失败静默(warm_predict 内部 guard/try)。
    from ..services.predictor import warm_predict

    warm_predict()
