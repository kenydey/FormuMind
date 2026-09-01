"""Structure-image recognition pipeline — image → SMILES → MolJSON → similar hits.

Wires together the pieces already deployed:
  MolScribe (container, celery ``molscribe`` queue)  image → SMILES
  moljson.validate_smiles / smiles_to_moljson        structural integrity + LLM-ready JSON
  structure_search.similarity_hits                   catalog Tanimoto ranking

Image bytes are written to the shared volume ``/app/data`` (both backend and
molscribe containers mount it) so the recognizer can open the file — writing
to container-local ``/tmp`` fails with OpenCV ``!src.empty()``.
"""
from __future__ import annotations

import hashlib
import logging
import os

from ..config import get_settings

logger = logging.getLogger(__name__)

_SHARED_DIR = "/app/data/_structure_tmp"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_MAGIC = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"RIFF": "webp",
}


def _detect_image_type(content: bytes) -> str | None:
    for magic, ext in _ALLOWED_MAGIC.items():
        if content.startswith(magic):
            return ext
    return None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _cache_get(sha: str, settings) -> dict | None:
    try:
        from ..worker.task_progress import _redis_client

        rc = _redis_client()
        if rc is None:
            return None
        raw = rc.get(f"struct:{sha}")
        if not raw:
            return None
        import json

        return json.loads(raw)
    except Exception as exc:
        logger.debug("structure cache get failed: %s", exc)
        return None


def _cache_put(sha: str, payload: dict, settings, ttl: int = 604800) -> None:
    try:
        from ..worker.task_progress import _redis_client

        rc = _redis_client()
        if rc is None:
            return
        import json

        rc.setex(f"struct:{sha}", ttl, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:
        logger.debug("structure cache put failed: %s", exc)


def recognize_structure_image(
    content: bytes,
    *,
    filename: str = "structure.png",
    threshold: float = 0.6,
    top_k: int = 5,
    settings=None,
) -> dict:
    """Full pipeline for an uploaded structure image.

    Returns::

        {
          "recognized": bool,
          "smiles": str | None,
          "moljson": dict | None,
          "hits": [{"name", "role", "smiles", "similarity"}...],
          "image_sha": str,
          "cached": bool,
          "warnings": [str...],
          "error": str | None,
        }

    Never raises — degrades to ``recognized=False`` + warning on any failure
    (caller decides whether to block or fall back to text-only retrieval).
    """
    settings = settings or get_settings()
    warnings: list[str] = []
    err: str | None = None

    if not content:
        return _result(False, None, None, [], "", False, warnings, "空图片")
    if len(content) > _MAX_BYTES:
        return _result(False, None, None, [], "", False, warnings, f"图片超过 {_MAX_BYTES // 1024 // 1024}MB 限制")
    if _detect_image_type(content) is None:
        return _result(False, None, None, [], "", False, warnings, "仅支持 PNG/JPG/WebP 图片")

    sha = _sha256(content)
    cached = _cache_get(sha, settings)
    if cached:
        logger.info("structure image cache hit: %s…", sha[:8])
        return {**cached, "cached": True}

    # ── write to shared volume ──────────────────────────────────────
    try:
        os.makedirs(_SHARED_DIR, exist_ok=True)
        path = os.path.join(_SHARED_DIR, f"struct_{sha[:12]}.png")
        with open(path, "wb") as f:
            f.write(content)
    except OSError as exc:
        logger.warning("structure image write failed: %s", exc)
        return _result(False, None, None, [], sha, False, warnings, "图片暂存失败")

    # ── dispatch to MolScribe worker ────────────────────────────────
    try:
        from app.worker.celery_app import celery_app

        res = celery_app.send_task(
            "formumind.molscribe_recognize",
            args=[{"image_path": path}],
            queue=settings.molscribe_queue,
        ).get(timeout=settings.molscribe_timeout_s)
    except Exception as exc:
        logger.warning("molscribe dispatch failed: %s", exc)
        err = "结构识别服务不可用（MolScribe 未就绪或超时）"
        warnings.append(err)
        return _result(False, None, None, [], sha, False, warnings, err)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if not (res and res.get("ok") and res.get("smiles")):
        reason = (res or {}).get("reason") or "识别失败"
        err = f"未能识别结构：{reason}"
        warnings.append("该图片未能识别为单一分子结构（可能是聚合物/混合物图），可继续用文字提问")
        return _result(False, None, None, [], sha, False, warnings, err)

    smiles = res["smiles"]
    # ── structural validation (RDKit round-trip) ─────────────────────
    try:
        from ..services.moljson import validate_smiles

        info = validate_smiles(smiles)
    except Exception as exc:
        logger.warning("validate_smiles failed: %s", exc)
        info = {"valid": False}
    if not info.get("valid"):
        warnings.append(f"识别结果未通过结构校验（{smiles}），已丢弃")
        return _result(False, None, None, [], sha, False, warnings, "识别结果结构无效")

    # ── MolJSON for LLM reasoning ────────────────────────────────────
    moljson = None
    try:
        from ..services.moljson import smiles_to_moljson

        moljson = smiles_to_moljson(smiles)
    except Exception as exc:
        logger.debug("moljson conversion failed (non-fatal): %s", exc)

    # ── similar-catalog hits ─────────────────────────────────────────
    hits: list[dict] = []
    try:
        from ..services.structure_search import similarity_hits

        hits = similarity_hits(
            smiles, top_k=top_k, threshold=threshold, settings=settings
        )
    except Exception as exc:
        logger.warning("structure similarity scan failed: %s", exc)
        warnings.append("相似材料扫描失败（不影响识别结果）")

    payload = _result(True, smiles, moljson, hits, sha, False, warnings, None)
    _cache_put(sha, payload, settings)
    return payload


def _result(
    recognized: bool,
    smiles: str | None,
    moljson: dict | None,
    hits: list[dict],
    image_sha: str,
    cached: bool,
    warnings: list[str],
    error: str | None,
) -> dict:
    return {
        "recognized": recognized,
        "smiles": smiles,
        "moljson": moljson,
        "hits": hits,
        "image_sha": image_sha,
        "cached": cached,
        "warnings": warnings,
        "error": error,
    }
