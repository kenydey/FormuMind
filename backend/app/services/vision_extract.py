"""Image → structured JSON via the configured vision LLM (multimodal RAG).

The "降维打击"路线: instead of maintaining a local OCR stack (PaddleOCR /
GOT-OCR / Surya), uploaded images go to the *already configured* LLM provider
with a vision-capable model.  The model returns structured JSON — tables as
Markdown, molecule structure drawings as SMILES — and every SMILES claim is
verified locally with RDKit (parse → canonicalize); unverifiable claims are
kept but flagged, never silently trusted.

Providers: every OpenAI-compatible vendor (openai / deepseek / qwen / moonshot
/ xai / groq / custom base_url) via image content parts, plus Anthropic via
base64 image blocks.  Degrades to ``(None, hint)`` without a key / vision
model — ingestion then stores a placeholder instead of failing.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from ..config import get_settings
from ..domain.multimodal_schemas import VisionTableExtraction
from .errors import degrade_return
from .kg.prompts import build_multimodal_table_prompt
from .llm_roles import CUSTOM_PROVIDER, VISION, RoleConfig, is_warming, resolve_role
from .runtime_secrets import effective_setting

logger = logging.getLogger(__name__)

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}

IMAGE_EXTS = frozenset(_MIME_BY_EXT)

_VISION_SYSTEM = """你是化学文献图片结构化专家。分析图片并只输出一个 JSON 对象（无 markdown 围栏），字段：
- kind: "table" | "structure" | "flowchart" | "equation" | "document" | "other"
- markdown: 图片内容的完整 Markdown 表示——表格用 | 语法逐格转录；流程图用有序列表；
  文档截图逐段转录；反应方程式用 LaTeX（$$…$$）
- molecules: 图中出现的分子结构，[{"smiles": "...", "name": "…", "confidence": 0-1}]；
  没有分子结构图则为空数组；不确定的结构给低 confidence，绝不编造
- notes: 无法转录的内容说明（如模糊区域）
数值、化学式必须忠实转录，绝不允许捏造。"""


class VisionMolecule(BaseModel):
    smiles: str = ""
    name: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    verified: bool = False  # RDKit-parsed + canonicalized


class VisionExtraction(BaseModel):
    kind: Literal["table", "structure", "flowchart", "equation", "document", "other"] = "other"
    markdown: str = ""
    molecules: list[VisionMolecule] = Field(default_factory=list)
    notes: str = ""


# DeepSeek ships a vision model (`deepseek-v4-flash-vision-exp`) but its text
# models (deepseek-v4-pro / v4-flash) still reject an `image_url` content part
# with a 400 ("This model does not support image"). So the guard is model-level
# for deepseek: a vision model passes, a text model gets a hint naming the
# vision model. Every other OpenAI-compatible vendor (qwen / moonshot) is left
# to fail at the call with the provider's own message rather than be guessed at.
_DEEPSEEK_VISION_MARKER = "vision"


def _is_deepseek_vision(model: str) -> bool:
    """DeepSeek models that accept images carry ``vision`` in their id."""
    return _DEEPSEEK_VISION_MARKER in (model or "").lower()


def vision_available() -> tuple[bool, str]:
    """(configured, hint) — key present, provider not known-hostile, SDK installed.

    Note what this does **not** claim: that the configured model can actually
    read a picture. For a self-hosted or rented endpoint that is unknowable from
    here — we have no way to tell which weights the user deployed — and a
    capability table we cannot keep accurate is exactly the lying probe this
    codebase has been bitten by four times. So ``custom`` follows the policy
    already documented for qwen/moonshot below: don't guess, let the call fail
    carrying the provider's own message. ``POST /api/settings/vision/test`` is
    the way to actually *know*.

    The SDK check is not a formality. Without it this returned ``(True, "")``
    on a machine with no ``openai`` package, so callers were told vision was
    available and every image came back as ``No module named 'openai'`` — the
    same "installed but cannot work" gap that made uploads silently index
    nothing. An availability probe that answers yes when the next call cannot
    succeed is worse than no probe, because the caller stops planning for the
    failure.
    """
    from .errors import optional_import

    settings = get_settings()
    if not effective_setting(settings, "vision_extract_enabled"):
        return False, "图片视觉解析已禁用（FORMUMIND_VISION_EXTRACT_ENABLED）"

    cfg = resolve_role(VISION)
    if not cfg.configured:
        where = "文本模型" if cfg.inherited else "视觉模型"
        return False, f"未配置「设置 → 大模型 → {where}」的 API key，无法调用视觉模型"
    if not cfg.model:
        return False, "未指定视觉模型名称（设置 → 大模型 → 视觉模型）"
    if cfg.provider == "gemini":
        return False, "Gemini 原生接口暂不支持图片解析——请切换 OpenAI 兼容供应商或 Anthropic"
    if cfg.provider == "deepseek" and not _is_deepseek_vision(cfg.model):
        hint = (
            "deepseek 文本模型（deepseek-v4-pro / v4-flash）不支持图片输入。"
            "请在「设置 → 大模型 → 视觉模型」指定 deepseek-v4-flash-vision-exp，"
            "或切换其他支持视觉的供应商（Claude / OpenAI / 自定义端点），"
            "文本模型可保持不变。"
        )
        return False, hint

    # Anthropic has its own client; every other supported provider is reached
    # through the OpenAI-compatible one.
    if cfg.provider == "anthropic":
        if not optional_import("anthropic"):
            return False, "未安装 anthropic SDK（pip install anthropic 或安装 llm extra）"
    elif not optional_import("openai"):
        return False, (
            f"未安装 openai SDK，无法调用 {cfg.provider} 的视觉接口"
            "（pip install openai 或安装 llm extra）"
        )
    return True, ""


def _strip_json_fences(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.DOTALL)
    return m.group(0) if m else t


def _call_openai_vision(
    prompt: str, image_b64: str, mime: str, *, api_key: str, model: str,
    base_url: str | None, max_tokens: int, timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> str:
    from openai import OpenAI  # type: ignore

    kwargs: dict = {"api_key": api_key, "timeout": timeout}
    if base_url:
        kwargs["base_url"] = base_url
    if extra_headers:
        kwargs["default_headers"] = dict(extra_headers)
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            }
        ],
    )
    return resp.choices[0].message.content or ""


def _call_anthropic_vision(
    prompt: str, image_b64: str, mime: str, *, api_key: str, model: str,
    max_tokens: int, timeout: float,
) -> str:
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": image_b64},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _call_vision(cfg: RoleConfig, prompt: str, content: bytes, filename: str) -> str:
    """Send one image + prompt to the resolved vision role, return raw text.

    The two public entry points used to each carry their own copy of the
    provider/key/model/base_url/timeout resolution and the anthropic-vs-OpenAI
    branch. Both now share this, so a change to how vision is reached cannot
    apply to one caller and miss the other.
    """
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "png").lower()
    mime = _MIME_BY_EXT.get(ext, "image/png")
    image_b64 = base64.b64encode(content).decode("ascii")

    if cfg.provider == "anthropic":
        return _call_anthropic_vision(
            prompt, image_b64, mime,
            api_key=cfg.api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, timeout=cfg.timeout,
        )
    return _call_openai_vision(
        prompt, image_b64, mime,
        api_key=cfg.api_key, model=cfg.model, base_url=cfg.base_url,
        max_tokens=cfg.max_tokens, timeout=cfg.timeout,
        extra_headers=cfg.extra_headers,
    )


def _failure_hint(exc: Exception) -> str:
    """Turn a provider exception into something worth reading.

    A 503 from a scale-to-zero endpoint means "not running yet", not "broken" —
    saying so is the difference between waiting a minute and going to hunt for a
    bad API key.
    """
    if is_warming(exc):
        return "视觉端点正在冷启动（503），副本就绪后重试即可"
    return str(exc)[:200]


def _is_permanent_vision_failure(error: str | None) -> bool:
    """Whether *error* describes a condition retrying cannot fix.

    The upstream vision endpoint reports "endpoint is paused" as a 400
    (not a 503), and a revoked token returns 401/403.  Retrying either is
    strictly waste — the answer will not change until an operator acts.
    A transient network error, timeout, or 503 cold-start is NOT permanent
    and should not trip the breaker.
    """
    if not error:
        return False
    lower = error.lower()
    permanent = (
        "endpoint is paused",
        "bad request",
        "bad_request",
        "account",
        "suspended",
        "forbidden",
        "unauthorized",
        "insufficient_quota",
        "quota has been exhausted",
    )
    return any(marker in lower for marker in permanent)


def prewarm() -> tuple[bool, float, str]:
    """Wake a scale-to-zero endpoint once, before a batch of figures.

    Returns ``(ok, seconds, hint)``. A no-op returning ``(True, 0.0, "")`` for
    every provider except a custom endpoint, so callers need no knowledge of who
    is serving vision.

    Why bother: with an endpoint scaled to zero the first real call absorbs a
    multi-minute boot. Paying that inside the per-page loop makes the wait look
    like "page 3 is mysteriously slow"; paying it here attributes it correctly in
    the log and keeps the per-page timings meaningful. It costs one tiny request.

    Best-effort by contract — a failure here is not a reason to skip the figures,
    because the per-page calls will each try anyway and may well succeed.
    """
    import time

    ok, hint = vision_available()
    if not ok:
        return False, 0.0, hint
    cfg = resolve_role(VISION)
    if cfg.provider != CUSTOM_PROVIDER:
        return True, 0.0, ""

    started = time.monotonic()
    try:
        from openai import OpenAI  # type: ignore

        kwargs: dict = {"api_key": cfg.api_key, "timeout": cfg.timeout}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        if cfg.extra_headers:
            kwargs["default_headers"] = dict(cfg.extra_headers)
        OpenAI(**kwargs).chat.completions.create(
            model=cfg.model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
    except Exception as exc:
        elapsed = time.monotonic() - started
        if is_warming(exc):
            hint = "视觉端点仍在冷启动中（副本尚未就绪）"
        else:
            hint = str(exc)[:200]
        logger.warning("vision prewarm failed after %.1fs: %s", elapsed, hint)
        return False, elapsed, hint

    elapsed = time.monotonic() - started
    logger.info("vision endpoint warm after %.1fs (%s)", elapsed, cfg.model)
    return True, elapsed, ""


def _tiny_png(side: int = 8) -> bytes:
    """A valid solid-grey PNG, built without Pillow.

    Hand-rolled rather than a hardcoded base64 blob so it stays readable, and
    8×8 rather than 1×1 because some vision stacks reject degenerate images —
    a probe that fails for its own reasons teaches nothing about the endpoint.
    """
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # 8-bit greyscale, no interlace. Each row is prefixed with its filter byte.
    ihdr = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([0x80] * side) for _ in range(side))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def probe_vision() -> dict:
    """Actually call the vision model and report what happened.

    ``vision_available()`` answers "is it configured"; this answers "does it
    work", which for a bring-your-own endpoint is the only honest way to know.
    """
    import time

    ok, hint = vision_available()
    if not ok:
        return {"ok": False, "provider": "", "model": "", "message": hint}

    cfg = resolve_role(VISION)
    base = {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "inherits": cfg.inherited,
    }
    started = time.monotonic()
    try:
        raw = _call_vision(
            cfg,
            "这是一张纯色测试图。只回复两个字符：OK",
            _tiny_png(),
            "probe.png",
        )
    except Exception as exc:
        logger.warning("vision probe failed (%s/%s): %s", cfg.provider, cfg.model, exc)
        return {**base, "ok": False, "message": _failure_hint(exc)}

    elapsed = time.monotonic() - started
    text = (raw or "").strip()
    if not text:
        # Reached the model but got nothing usable — the endpoint is up and the
        # credentials work, so say that rather than implying a config error.
        return {
            **base,
            "ok": False,
            "message": f"端点已响应但未返回文本（{elapsed:.1f}s），请确认模型具备视觉能力",
        }
    return {
        **base,
        "ok": True,
        "message": f"视觉模型可用（{elapsed:.1f}s）：{text[:80]}",
    }


def _verify_molecules(molecules: list[VisionMolecule]) -> list[VisionMolecule]:
    """RDKit validation loop: parse → canonicalize → flag; drop empty claims."""
    try:
        from rdkit import Chem, RDLogger  # type: ignore

        RDLogger.DisableLog("rdApp.*")
        rdkit_ok = True
    except Exception:
        rdkit_ok = False

    out: list[VisionMolecule] = []
    for mol in molecules:
        smi = (mol.smiles or "").strip()
        if not smi and not mol.name:
            continue
        if smi and rdkit_ok:
            try:
                parsed = Chem.MolFromSmiles(smi)
            except Exception:
                parsed = None
            if parsed is not None:
                mol.smiles = Chem.MolToSmiles(parsed)
                mol.verified = True
            else:
                # Keep the claim (name may still be useful) but flag it.
                mol.confidence = min(mol.confidence, 0.3)
                mol.verified = False
        out.append(mol)
    return out


def _molecules_from_smiles(smiles: str) -> VisionExtraction:
    """把单个 SMILES 字符串包成 VisionExtraction（经 RDKit 验证兜底）。"""
    mol = VisionMolecule(smiles=(smiles or "").strip(), confidence=0.8)
    molecules = _verify_molecules([mol])
    return VisionExtraction(kind="structure", molecules=molecules)


def _decimer_direct(content: bytes, settings) -> VisionExtraction | None:
    """① DECIMER 离线直识（免 token，假设图已裁剪）。

    投递独立 decimer Celery worker 识别，成功即返回；任何失败返回 None
    （由调用方回退视觉 LLM）。
    """
    if not settings.decimer_enabled:
        return None
    try:
        # 延迟导入：避免主进程模块加载期拉起 Celery/TF 依赖链
        from app.worker.celery_app import celery_app
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(content)
            path = f.name
        try:
            res = celery_app.send_task(
                "formumind.decimer_recognize",
                args=[{"image_path": path}],
                queue=settings.decimer_queue,
            ).get(timeout=settings.decimer_timeout_s)
        finally:
            os.unlink(path)
        if res and res.get("ok"):
            return _molecules_from_smiles(res["smiles"])
    except Exception as exc:
        logger.warning("DECIMER direct path failed: %s", exc)
    return None


def extract_image(content: bytes, filename: str) -> tuple[VisionExtraction | None, str | None]:
    """Structured extraction for one image; (None, reason) when unavailable.

    结构图→SMILES 优先走 DECIMER 离线直识（免 token），失败回退视觉 LLM。
    """
    settings = get_settings()
    # ① DECIMER 离线直识（免 token，主路径）
    if settings.decimer_enabled:
        extraction = _decimer_direct(content, settings)
        if extraction is not None:
            return extraction, None

    # ④ 兜底：视觉 LLM（原逻辑不动）
    ok, hint = vision_available()
    if not ok:
        return None, hint
    cfg = resolve_role(VISION)
    prompt = f"{_VISION_SYSTEM}\n\n文件名：{filename}"

    try:
        raw = _call_vision(cfg, prompt, content, filename)
        data = json.loads(_strip_json_fences(raw))
        extraction = VisionExtraction.model_validate(data)
        extraction.molecules = _verify_molecules(extraction.molecules)
        return extraction, None
    except Exception as exc:
        return None, degrade_return(logger, exc, "vision extraction failed", _failure_hint(exc))


def extract_structured_table_from_image(
    image_bytes: bytes,
    filename: str = "table.png",
    context_text: str = "",
) -> tuple[VisionTableExtraction | None, str | None]:
    """Patent comparison-table extraction → structured formulation JSON.

    Synchronous API (matches the rest of vision_extract / ingestion). FastAPI
    routes may wrap this in ``run_in_threadpool`` when needed.
    """
    ok, hint = vision_available()
    if not ok:
        return None, hint
    cfg = resolve_role(VISION)
    prompt = build_multimodal_table_prompt(context_text)
    if filename:
        prompt = f"{prompt}\n\n文件名：{filename}"

    try:
        raw = _call_vision(cfg, prompt, image_bytes, filename)
        data = json.loads(_strip_json_fences(raw))
        if not isinstance(data, dict):
            return None, "视觉模型返回非对象 JSON"
        extraction = VisionTableExtraction.model_validate(data)
        if not extraction.formulations:
            return extraction, "未识别到配方行（formulations 为空）"
        return extraction, None
    except json.JSONDecodeError as exc:
        return None, degrade_return(logger, exc, "vision table JSON parse failed", str(exc)[:200])
    except Exception as exc:
        return None, degrade_return(
            logger, exc, "vision table extraction failed", _failure_hint(exc)
        )


def image_markdown(extraction: VisionExtraction, filename: str) -> str:
    """Render an extraction as the Markdown document that enters the KB."""
    kind_labels = {
        "table": "表格", "structure": "分子结构图", "flowchart": "流程图",
        "equation": "反应方程式", "document": "文档截图", "other": "图片",
    }
    parts = [f"# {filename}（{kind_labels.get(extraction.kind, '图片')}·视觉解析）"]
    if extraction.markdown.strip():
        parts.append(extraction.markdown.strip())
    if extraction.molecules:
        rows = ["| 分子 | SMILES | 置信度 | RDKit 验证 |", "|---|---|---|---|"]
        for mol in extraction.molecules:
            rows.append(
                f"| {mol.name or '—'} | `{mol.smiles or '—'}` "
                f"| {mol.confidence:.2f} | {'✓' if mol.verified else '✗'} |"
            )
        parts.append("## 识别的分子结构\n\n" + "\n".join(rows))
    if extraction.notes.strip():
        parts.append(f"> 备注：{extraction.notes.strip()}")
    return "\n\n".join(parts)
