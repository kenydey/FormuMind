"""v29: Embedding 模型后台下载服务。

支持从 HuggingFace 下载 embedding 模型，带进度跟踪和断点续传。
下载任务在后台线程运行，通过 task_id 查询进度。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 任务状态存储（内存，进程级）
_TASKS: dict[str, "DownloadTask"] = {}
_TASKS_LOCK = threading.Lock()


@dataclass
class DownloadTask:
    task_id: str
    model_id: str
    status: str = "pending"  # pending | downloading | done | failed
    progress: float = 0.0  # 0-100
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    done_at: float | None = None


def _hf_cache_dir() -> Path:
    return Path(os.path.expanduser("~/.cache/huggingface/hub"))


def is_model_cached(model_id: str) -> bool:
    """检查模型是否已在本地缓存。"""
    safe = "models--" + model_id.replace("/", "--")
    return (_hf_cache_dir() / safe).is_dir()


def get_model_size_estimate(model_id: str) -> str:
    """返回模型大小估计（用于 UI 显示）。"""
    # 硬编码已知模型大小，避免每次都查 HF API
    sizes = {
        "sentence-transformers/all-MiniLM-L6-v2": "~90MB",
        "BAAI/bge-small-zh-v1.5": "~100MB",
        "BAAI/bge-m3": "~2GB",
        "Qwen/Qwen3-Embedding-0.6B": "~1.2GB",
        "moka-ai/m3e-base": "~200MB",
    }
    return sizes.get(model_id, "未知")


def start_download(model_id: str) -> str:
    """启动后台下载，返回 task_id。"""
    task_id = str(uuid.uuid4())[:8]
    task = DownloadTask(task_id=task_id, model_id=model_id)
    with _TASKS_LOCK:
        _TASKS[task_id] = task
    thread = threading.Thread(
        target=_download_worker, args=(task,), daemon=True, name=f"emb-dl-{task_id}"
    )
    thread.start()
    return task_id


def get_task(task_id: str) -> DownloadTask | None:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def _download_worker(task: DownloadTask) -> None:
    task.status = "downloading"
    try:
        from huggingface_hub import snapshot_download

        def _progress(current: int, total: int) -> None:
            if total > 0:
                task.progress = round(current / total * 100, 1)

        # snapshot_download 支持断点续传
        snapshot_download(
            repo_id=task.model_id,
            # 只下载必要文件，跳过 .bin 以外的冗余
            allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors", "*.bin"],
        )
        task.status = "done"
        task.progress = 100.0
        task.done_at = time.time()
        logger.info("模型下载完成: %s", task.model_id)
    except Exception as exc:
        task.status = "failed"
        task.error = str(exc)[:500]
        task.done_at = time.time()
        logger.warning("模型下载失败 %s: %s", task.model_id, exc)
