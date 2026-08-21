"""DECIMER offline OCSR adapter — soft dependency, no-op when absent.

化学结构图 → SMILES 的离线识别引擎。DECIMER（TensorFlow）只安装在独立
decimer Celery worker 的 venv 里，主 backend 进程永不 import tensorflow；
这里的 ``decimer_available()`` 在主进程恒为 False，只有 decimer worker 内为 True。

设计沿用 chemtools.py 的软依赖模式：缺库 / 开关关闭时所有调用返回中性值，
管线行为不变。
"""
from __future__ import annotations

import logging

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def decimer_available() -> bool:
    """当前进程能否 import DECIMER（仅独立 decimer worker 为 True）。"""
    try:
        __import__("DECIMER")
        return True
    except Exception:
        return False


def resolve_decimer_mode(settings: Settings | None = None) -> str:
    """auto → 探测 GPU；显式 gpu/cpu 直接返回。"""
    s = settings or get_settings()
    mode = (s.decimer_mode or "auto").strip().lower()
    if mode in ("gpu", "cpu"):
        return mode
    # auto：探测 CUDA（仅 decimer worker 内 tensorflow 可达；主进程落入 except）
    try:
        import tensorflow as tf  # noqa: F401
        if tf.config.list_physical_devices("GPU"):
            return "gpu"
    except Exception:
        pass
    return "cpu"


def predict_smiles_local(image_path: str) -> str | None:
    """同进程直接识别（仅当 decimer 已装在当前 venv，即 decimer worker 内）。

    返回 SMILES 字符串；识别失败 / 库缺失返回 None。
    """
    if not decimer_available():
        return None
    try:
        from DECIMER import predict_SMILES  # noqa: F401

        return predict_SMILES(image_path)
    except Exception as exc:
        logger.warning("DECIMER predict_SMILES failed: %s", exc)
        return None


def availability() -> dict:
    """Per-capability 报告（供 /api/chemical/tools 或 settings UI）。"""
    s = get_settings()
    mode = resolve_decimer_mode(s)
    return {
        "enabled": s.decimer_enabled,
        "mode": mode,
        "installed_in_process": decimer_available(),
        "queue": s.decimer_queue,
        "segmentation": mode == "gpu",
        "timeout_s": s.decimer_timeout_s,
    }
