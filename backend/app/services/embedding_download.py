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
    """The Hugging Face hub cache, honouring the same variables huggingface_hub does."""
    explicit = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    return Path(os.path.expanduser("~/.cache/huggingface/hub"))


_WEIGHT_PATTERNS = ("*.safetensors", "pytorch_model*.bin", "model*.bin", "*.onnx")


def is_model_cached(model_id: str) -> bool:
    """True when a *complete* snapshot of the model is in the local cache.

    The ``models--<id>`` directory alone proves nothing: an interrupted download leaves it behind. huggingface_hub only
    links a file into ``snapshots/<rev>/`` once it has fully arrived, so a usable snapshot has its ``config.json`` and
    at least one weight file there.
    """
    root = _hf_cache_dir() / ("models--" + model_id.replace("/", "--")) / "snapshots"
    if not root.is_dir():
        return False
    for snap in root.iterdir():
        if snap.is_dir() and (snap / "config.json").exists() and any(
            next(snap.glob(pat), None) is not None for pat in _WEIGHT_PATTERNS
        ):
            return True
    return False


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
    """启动后台下载，返回 task_id。已完整缓存的模型直接得到 done 任务，不起线程、不联网。"""
    task_id = str(uuid.uuid4())[:8]
    task = DownloadTask(task_id=task_id, model_id=model_id)
    if is_model_cached(model_id):
        task.status = "done"
        task.progress = 100.0
        task.done_at = time.time()
        with _TASKS_LOCK:
            _TASKS[task_id] = task
        return task_id
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
