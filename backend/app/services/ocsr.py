"""OCSR 多后端 adapter — 无 GPU 走 MolScribe（torch），有 GPU 走 DECIMER（TF）。

统一「化学结构图 → SMILES」的离线识别接口。两个后端都只在独立 Celery
worker 的 venv 里 import（MolScribe 锁 numpy<2.0，DECIMER 是 TF，均与主 venv
冲突）；主 backend 进程这里的 ``*_available()`` 恒为 False，只在对应 worker 内
为 True。

设计沿用 decimer_ocr.py 的软依赖模式：缺库 / 开关关闭时所有调用返回中性值，
管线行为不变。
"""
from __future__ import annotations

import logging

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def molscribe_available() -> bool:
    """当前进程能否 import MolScribe（仅独立 molscribe worker 为 True）。"""
    try:
        __import__("molscribe")
        return True
    except Exception:
        return False


def decimer_available() -> bool:
    """当前进程能否 import DECIMER（仅独立 decimer worker 为 True）。"""
    from .decimer_ocr import decimer_available as _decimer_available

    return _decimer_available()


def resolve_ocsr_backend(settings: Settings | None = None) -> str:
    """auto → 探测 GPU：有 GPU → decimer，无 GPU → molscribe；显式值直接返回。"""
    s = settings or get_settings()
    backend = (s.ocsr_backend or "auto").strip().lower()
    if backend in ("molscribe", "decimer"):
        return backend
    # auto：探测 CUDA（主进程 torch 是 cpu 版 → False；decimer worker 内 tensorflow 可达）
    try:
        import torch  # noqa: F401

        if torch.cuda.is_available():
            return "decimer"
    except Exception:
        pass
    try:
        import tensorflow as tf  # noqa: F401

        if tf.config.list_physical_devices("GPU"):
            return "decimer"
    except Exception:
        pass
    return "molscribe"


# ── MolScribe 后端 ─────────────────────────────────────────────────────────
_molscribe_model = None


def _get_molscribe_model():
    """懒加载 MolScribe 模型（Swin-B，~350MB checkpoint）。worker 常驻摊薄。"""
    global _molscribe_model
    if _molscribe_model is None:
        import torch
        from molscribe import MolScribe
        from huggingface_hub import hf_hub_download

        ckpt = hf_hub_download("yujieq/MolScribe", "swin_base_char_aux_1m.pth")
        _molscribe_model = MolScribe(ckpt, device=torch.device("cpu"))
    return _molscribe_model


def predict_smiles_molscribe(image_path: str) -> str | None:
    """MolScribe 识别单张结构图 → SMILES。失败 / 缺库返回 None。"""
    if not molscribe_available():
        return None
    try:
        model = _get_molscribe_model()
        out = model.predict_image_file(image_path)
        return (out or {}).get("smiles") or None
    except Exception as exc:
        logger.warning("MolScribe predict failed: %s", exc)
        return None


def prewarm_molscribe() -> bool:
    """molscribe worker 启动时预加载模型，避免冷启动落在首个任务上。"""
    if not molscribe_available():
        return False
    try:
        _get_molscribe_model()
        return True
    except Exception as exc:
        logger.warning("MolScribe prewarm failed: %s", exc)
        return False


# ── 统一分发入口 ───────────────────────────────────────────────────────────
def predict_smiles_local(image_path: str, backend: str | None = None) -> str | None:
    """按后端分发离线识别。backend 缺省时按 settings.ocsr_backend 解析。"""
    b = (backend or resolve_ocsr_backend()).strip().lower()
    if b == "molscribe":
        return predict_smiles_molscribe(image_path)
    if b == "decimer":
        from .decimer_ocr import predict_smiles_local as _decimer

        return _decimer(image_path)
    return None


def availability() -> dict:
    """多后端状态报告（供 /api/settings/ocsr 或设置 UI）。"""
    s = get_settings()
    return {
        "enabled": s.decimer_enabled,
        "backend": resolve_ocsr_backend(s),
        "ocsr_backend": s.ocsr_backend,
        "molscribe_installed": molscribe_available(),
        "decimer_installed": decimer_available(),
        "molscribe_queue": s.molscribe_queue,
        "decimer_queue": s.decimer_queue,
        "molscribe_timeout_s": s.molscribe_timeout_s,
        "decimer_timeout_s": s.decimer_timeout_s,
    }
