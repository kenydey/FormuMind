"""Parse/retrieval hardware profiles — one-click presets for low/mid/high.

2026-09-05 (plan 2026-09-05-parsing-rag-profiles.md): 三档一键配置, 全部映射
已有底层开关(env-flags 注册表 + pdf_parser/rag_backend env), 不发明新 env。

- low  : 纯 CPU —— hybrid 本地解析(云 MinerU 关) + bm25_faiss 检索
- mid  : CPU + 可选云 —— low + 云 MinerU 页升级(需 token) + gpu_enabled
         (auto: 无 CUDA 自动落 bm25_faiss)
- high : GPU 主机 —— pdf_parser=mineru(本地 magic-pdf) + gpu_enabled(auto
         → pylate) + 本地版面 OCR

应用动作: 写 os.environ(立即生效, config 已缓存值经 cache_clear 失效) +
secrets_store.write_env_updates 持久化(只读 FS 时静默跳过, 同 formulation_mode)。
"""
from __future__ import annotations

import logging

from . import secrets_store

logger = logging.getLogger(__name__)

PROFILE_NAMES = ("low", "mid", "high")

# profile → (env-flag booleans, {str env: value})
_PROFILES: dict[str, tuple[dict[str, bool], dict[str, str]]] = {
    "low": (
        {
            "gpu_enabled": False,
            "mineru_enabled": False,
            "mineru_batch_enabled": False,
            "pdf_local_ocr": False,
            "pdf_ocr": True,
            "rapidocr_enabled": True,
        },
        {"FORMUMIND_PDF_PARSER": "auto", "FORMUMIND_RAG_BACKEND": "auto"},
    ),
    "mid": (
        {
            "gpu_enabled": True,  # auto 降级: 无 CUDA → bm25_faiss
            "mineru_enabled": True,  # 云 MinerU, 需 token; 无 token 时降级等同 low
            "mineru_batch_enabled": False,
            "pdf_local_ocr": False,
            "pdf_ocr": True,
            "rapidocr_enabled": True,
        },
        {"FORMUMIND_PDF_PARSER": "auto", "FORMUMIND_RAG_BACKEND": "auto"},
    ),
    "high": (
        {
            "gpu_enabled": True,
            "mineru_enabled": False,  # 本地 magic-pdf 取代云
            "mineru_batch_enabled": False,
            "pdf_local_ocr": True,
            "pdf_ocr": True,
            "rapidocr_enabled": True,
        },
        {"FORMUMIND_PDF_PARSER": "mineru", "FORMUMIND_RAG_BACKEND": "auto"},
    ),
}

_BOOL_ENV = {
    "gpu_enabled": "FORMUMIND_GPU_ENABLED",
    "mineru_enabled": "FORMUMIND_MINERU_ENABLED",
    "mineru_batch_enabled": "FORMUMIND_MINERU_BATCH_ENABLED",
    "pdf_local_ocr": "FORMUMIND_PDF_LOCAL_OCR",
    "pdf_ocr": "FORMUMIND_PDF_OCR",
    "rapidocr_enabled": "FORMUMIND_RAPIDOCR_ENABLED",
}


def probe_availability() -> dict:
    """Effective capability snapshot: what each tier can actually use right now."""
    from ..config import get_settings
    from ..services import colbert_store

    settings = get_settings()
    try:
        gpu_ok = bool(
            settings.gpu_enabled
            and colbert_store.colbert_available_gpu(settings)
        )
    except Exception:
        gpu_ok = False
    from ..services.runtime_secrets import effective_setting

    mineru_key = bool(effective_setting(settings, "mineru_api_key"))
    from ..services.vision_extract import vision_available

    vision_ok, _ = vision_available()
    return {
        "gpu_available": gpu_ok,
        "mineru_key_present": mineru_key,
        "vision_available": vision_ok,
        "active_rag_backend": _active_backend(),
    }


def _active_backend() -> str:
    from ..services.rag import active_rag_backend

    try:
        return active_rag_backend()
    except Exception:
        return "unknown"


def current_profile() -> str:
    """Best-effort classification of the live configuration."""
    from ..config import get_settings

    s = get_settings()
    parser = (s.pdf_parser or "auto").lower()
    if parser == "mineru":
        return "high"
    if s.mineru_enabled and s.gpu_enabled:
        return "mid"
    if s.mineru_enabled:
        return "mid"
    return "low"


def apply_profile(profile: str) -> dict:
    """Apply a profile: live env + persistence; returns applied state."""
    if profile not in _PROFILES:
        raise ValueError(f"profile 必须是 {list(_PROFILES)} 之一, 收到 {profile!r}")
    flags, str_env = _PROFILES[profile]

    import os

    env_updates: dict[str, str] = {_BOOL_ENV[a]: ("true" if flags[a] else "false") for a in flags}
    env_updates.update(str_env)

    for key, value in env_updates.items():
        os.environ[key] = value

    from ..config import get_settings

    get_settings.cache_clear()

    try:
        secrets_store.write_env_updates(env_updates)
        persisted = True
    except Exception as exc:  # read-only FS — live process env still applied
        logger.warning("profile env persistence skipped: %s", exc)
        persisted = False

    return {
        "profile": profile,
        "persisted": persisted,
        "env": env_updates,
        "availability": probe_availability(),
    }
