"""OCSR 离线结构识别 adapter — MolScribe（torch，独立 worker）。

统一「化学结构图 → SMILES」的离线识别接口。MolScribe 只在独立 Celery worker
的 venv 里 import（锁 numpy<2.0，与主 venv numpy 2.x 冲突）；主 backend 进程这里
的 ``molscribe_available()`` 恒为 False，所有调用返回中性值，管线行为不变。
"""
from __future__ import annotations

import logging
import time

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


def molscribe_available() -> bool:
    """当前进程能否 import MolScribe（仅独立 molscribe worker 为 True）。"""
    try:
        __import__("molscribe")
        return True
    except Exception:
        return False


# ── MolScribe 后端 ─────────────────────────────────────────────────────────
_molscribe_model = None


def _get_molscribe_model():
    global _molscribe_model
    if _molscribe_model is None:
        import torch
        from molscribe import MolScribe
        from huggingface_hub import hf_hub_download

        ckpt = hf_hub_download("yujieq/MolScribe", "swin_base_char_aux_1m.pth")
        _molscribe_model = MolScribe(ckpt, device=torch.device("cpu"))
    return _molscribe_model


def predict_smiles_molscribe(image_path: str) -> str | None:
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
    if not molscribe_available():
        return False
    try:
        _get_molscribe_model()
        return True
    except Exception as exc:
        logger.warning("MolScribe prewarm failed: %s", exc)
        return False


# ── 统一预测入口 ───────────────────────────────────────────────────────────
def predict_smiles_local(image_path: str) -> str | None:
    """离线识别入口（当前唯一后端 MolScribe）。缺库时中性返回 None。"""
    return predict_smiles_molscribe(image_path)


_alive_cache: dict = {"t": 0.0, "v": False}


def _molscribe_worker_alive() -> bool:
    """Whether a dedicated MolScribe Celery worker is consuming the queue.

    ``molscribe_available()`` only reports the *current* process, and the
    main backend never carries MolScribe (it lives in the dedicated worker
    image), so it would report False even when OCSR is fully operational.
    Probe the broker instead: ping all workers and look for the
    ``molscribe@`` name set by ``celery -n molscribe@%h``. Cached 30 s — the
    settings panel polls this.
    """
    now = time.monotonic()
    if now - _alive_cache["t"] < 30:
        return _alive_cache["v"]
    try:
        from ..worker.celery_app import celery_app

        pings = celery_app.control.ping(timeout=2) or {}
        alive = any("molscribe" in str(name).lower() for name in pings)
    except Exception:
        alive = False
    _alive_cache.update(t=now, v=alive)
    return alive


def availability() -> dict:
    s = get_settings()
    return {
        "enabled": s.ocsr_enabled,
        "molscribe_installed": _molscribe_worker_alive(),
        "molscribe_queue": s.molscribe_queue,
        "molscribe_timeout_s": s.molscribe_timeout_s,
    }
