"""RAG 冷启动预热：后台触发 ColBERT/rag 模型加载，避免首个 research 请求 10s+ 等待。

Design
-------
* ``preheat()`` 在后台线程触发一次性的模型加载（ColBERT ragatouille / rag store），
  重复调用幂等，已完成则直接返回 cached 状态。
* ``get_status()`` 供 ``GET /research/rag/status`` 透传 ``prewarm`` 字段，前端可用它
  判断是否仍在冷启动中（显示“模型冷启动中…”与真实进度区分）。
* 启动后由 ``lifespan`` 以 ``asyncio.create_task`` 非阻塞调度，不阻塞 uvicorn 就绪。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_state: dict[str, Any] = {
    "status": "idle",  # idle | warming | ready | failed
    "started_at": None,
    "finished_at": None,
    "backend": None,
    "error": None,
    "elapsed_ms": None,
}
_lock = threading.Lock()


def get_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _do_prewarm() -> None:
    from ..config import get_settings
    from ..services.colbert_store import active_backend

    settings = get_settings()
    backend = active_backend(settings)
    t0 = time.time()
    try:
        # 1. ColBERT / pylate 模型（仅当后端需要时）
        if backend in ("colbert", "pylate"):
            from ..services.colbert_store import _get_ragatouille_model  # type: ignore

            try:
                _get_ragatouille_model(settings)
            except Exception as exc:  # 模型缺失不视为失败，降级 fallback 仍可用
                logger.warning("rag prewarm colbert load failed (fallback ok): %s", exc)
        # 2. rag fallback store（轻量，构建倒排索引）
        try:
            from ..services import rag

            # 构建一次空 store 触发懒加载（BM25 索引等）
            rag.build_store()
        except Exception as exc:
            logger.warning("rag prewarm rag.build_store failed: %s", exc)
        elapsed = int((time.time() - t0) * 1000)
        with _lock:
            _state.update(status="ready", finished_at=time.time(), backend=backend, elapsed_ms=elapsed, error=None)
        logger.info("rag prewarm ready backend=%s elapsed=%dms", backend, elapsed)
    except Exception as exc:
        elapsed = int((time.time() - t0) * 1000)
        with _lock:
            _state.update(status="failed", finished_at=time.time(), backend=backend, elapsed_ms=elapsed, error=str(exc)[:300])
        logger.warning("rag prewarm failed: %s", exc)


def preheat(background: bool = True) -> dict[str, Any]:
    """触发预热，background=True 时在线程中异步执行并立即返回 warming 状态。"""
    with _lock:
        if _state["status"] in ("warming", "ready"):
            return dict(_state)
        _state.update(status="warming", started_at=time.time(), finished_at=None, error=None)
    if background:
        t = threading.Thread(target=_do_prewarm, name="rag-prewarm", daemon=True)
        t.start()
    else:
        _do_prewarm()
    with _lock:
        return dict(_state)
