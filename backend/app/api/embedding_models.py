"""v29: Embedding 模型管理 API。

- GET /api/embedding-models: 列出 5 个可选模型（含下载状态）
- POST /api/embedding-models/download: 后台下载模型
- GET /api/embedding-models/tasks/{task_id}: 查询任务状态
- POST /api/embedding-models/switch: 切换模型并重建索引
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import embedding_download
from ..services.rag import EMBEDDING_MODEL_CATALOG, embed_model_name

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/embedding-models", tags=["embedding-models"])

# 切换任务状态（内存）
_SWITCH_TASK: dict = {"status": "idle", "progress": 0.0, "error": None}
_SWITCH_LOCK = threading.Lock()


class EmbeddingModelInfo(BaseModel):
    id: str
    label: str
    langs: str
    note: str
    dim: int
    size_estimate: str
    cached: bool
    current: bool


class DownloadRequest(BaseModel):
    model_id: str


class DownloadResponse(BaseModel):
    task_id: str
    model_id: str


class TaskStatus(BaseModel):
    task_id: str
    model_id: str
    status: str
    progress: float
    error: str | None = None


class SwitchRequest(BaseModel):
    model_id: str


class SwitchResponse(BaseModel):
    status: str
    message: str


# 模型维度映射
_MODEL_DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-m3": 1024,
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "moka-ai/m3e-base": 768,
}


@router.get("", response_model=list[EmbeddingModelInfo])
def list_models():
    """列出 5 个可选 embedding 模型及其状态。"""
    current = embed_model_name()
    result = []
    for row in EMBEDDING_MODEL_CATALOG:
        mid = row["id"]
        result.append(
            EmbeddingModelInfo(
                id=mid,
                label=row["label"],
                langs=row["langs"],
                note=row["note"],
                dim=_MODEL_DIMS.get(mid, 0),
                size_estimate=embedding_download.get_model_size_estimate(mid),
                cached=embedding_download.is_model_cached(mid),
                current=(mid == current),
            )
        )
    return result


@router.post("/download", response_model=DownloadResponse)
def download_model(req: DownloadRequest):
    """后台下载模型，返回 task_id 用轮询进度。"""
    valid_ids = {row["id"] for row in EMBEDDING_MODEL_CATALOG}
    if req.model_id not in valid_ids:
        from fastapi import HTTPException

        raise HTTPException(400, f"未知模型: {req.model_id}")
    if embedding_download.is_model_cached(req.model_id):
        # 已缓存，直接返回 done 状态的 task
        task_id = embedding_download.start_download(req.model_id)
        # 标记为 done（实际不会下载）
        task = embedding_download.get_task(task_id)
        if task:
            task.status = "done"
            task.progress = 100.0
        return DownloadResponse(task_id=task_id, model_id=req.model_id)
    task_id = embedding_download.start_download(req.model_id)
    return DownloadResponse(task_id=task_id, model_id=req.model_id)


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def task_status(task_id: str):
    from fastapi import HTTPException

    task = embedding_download.get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return TaskStatus(
        task_id=task.task_id,
        model_id=task.model_id,
        status=task.status,
        progress=task.progress,
        error=task.error,
    )


@router.post("/switch", response_model=SwitchResponse)
def switch_model(req: SwitchRequest):
    """切换 embedding 模型并重建索引（阻塞式后台任务）。

    前置：模型必须已下载（cached=True）。
    切换后触发全库重建索引，期间检索阻塞（按需求）。
    """
    valid_ids = {row["id"] for row in EMBEDDING_MODEL_CATALOG}
    if req.model_id not in valid_ids:
        from fastapi import HTTPException

        raise HTTPException(400, f"未知模型: {req.model_id}")
    if not embedding_download.is_model_cached(req.model_id):
        from fastapi import HTTPException

        raise HTTPException(400, f"模型未下载，请先下载: {req.model_id}")
    with _SWITCH_LOCK:
        if _SWITCH_TASK["status"] == "running":
            from fastapi import HTTPException

            raise HTTPException(409, "已有切换任务在运行")
        _SWITCH_TASK.update(status="running", progress=0.0, error=None)
    thread = threading.Thread(
        target=_switch_worker, args=(req.model_id,), daemon=True, name="emb-switch"
    )
    thread.start()
    return SwitchResponse(status="started", message=f"开始切换到 {req.model_id}，重建索引中")


@router.get("/switch/status")
def switch_status():
    with _SWITCH_LOCK:
        return dict(_SWITCH_TASK)


def _switch_worker(model_id: str) -> None:
    """后台切换：更新配置 + 重建索引。"""
    from ..services.rag import set_runtime_embedding_model

    # C P2-2: 记录旧模型，重建失败时回滚 —— 否则 override 已切、语料半迁移。
    old_model: str | None = None
    try:
        from ..services.rag import embed_model_name

        old_model = embed_model_name()
    except Exception:
        pass
    try:
        # 1. 更新运行时配置
        set_runtime_embedding_model(model_id)
        with _SWITCH_LOCK:
            _SWITCH_TASK["progress"] = 5.0

        # 2. 重建索引（调用泛化脚本逻辑）
        from ..services.embedding_reindex import reindex_for_model

        def _prog(p: float) -> None:
            with _SWITCH_LOCK:
                # 5% ~ 95% 给重建
                _SWITCH_TASK["progress"] = 5.0 + p * 0.9

        reindex_for_model(model_id, progress_cb=_prog)

        with _SWITCH_LOCK:
            _SWITCH_TASK.update(status="done", progress=100.0)
        logger.info("模型切换完成: %s", model_id)
    except Exception as exc:
        logger.warning("模型切换失败 %s: %s", model_id, exc)
        # C P2-2: 回滚 runtime override，避免半迁移状态。
        if old_model:
            try:
                set_runtime_embedding_model(old_model)
                logger.info("模型切换失败，已回滚到 %s", old_model)
            except Exception:
                pass
        with _SWITCH_LOCK:
            _SWITCH_TASK.update(
                status="failed", error=str(exc)[:500],
                partial=True,  # 语料可能半迁移，需手动恢复 .db.bak
            )
