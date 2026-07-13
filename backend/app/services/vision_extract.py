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
from .errors import degrade_return
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


def vision_available() -> tuple[bool, str]:
    """(usable, hint) — key present + provider reachable by this module."""
    settings = get_settings()
    if not settings.vision_extract_enabled:
        return False, "图片视觉解析已禁用（FORMUMIND_VISION_EXTRACT_ENABLED）"
    if not settings.get_active_api_key():
        return False, "未配置 LLM API key，无法调用视觉模型"
    provider = str(effective_setting(settings, "llm_provider"))
    if provider == "gemini":
        return False, "Gemini 原生接口暂不支持图片解析——请切换 OpenAI 兼容供应商或 Anthropic"
    return True, ""


def _vision_model(settings) -> str:
    return (settings.vision_model or "").strip() or str(
        effective_setting(settings, "llm_model")
    )


def _strip_json_fences(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.DOTALL)
    return m.group(0) if m else t


def _call_openai_vision(
    prompt: str, image_b64: str, mime: str, *, api_key: str, model: str,
    base_url: str | None, max_tokens: int, timeout: float,
) -> str:
    from openai import OpenAI  # type: ignore

    kwargs: dict = {"api_key": api_key, "timeout": timeout}
    if base_url:
        kwargs["base_url"] = base_url
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


def extract_image(content: bytes, filename: str) -> tuple[VisionExtraction | None, str | None]:
    """Structured extraction for one image; (None, reason) when unavailable."""
    ok, hint = vision_available()
    if not ok:
        return None, hint
    settings = get_settings()
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "png").lower()
    mime = _MIME_BY_EXT.get(ext, "image/png")
    image_b64 = base64.b64encode(content).decode("ascii")
    provider = str(effective_setting(settings, "llm_provider"))
    api_key = settings.get_active_api_key() or ""
    model = _vision_model(settings)
    timeout = float(settings.llm_timeout_seconds)
    max_tokens = int(settings.llm_max_tokens)
    prompt = f"{_VISION_SYSTEM}\n\n文件名：{filename}"

    try:
        if provider == "anthropic":
            raw = _call_anthropic_vision(
                prompt, image_b64, mime,
                api_key=api_key, model=model, max_tokens=max_tokens, timeout=timeout,
            )
        else:
            from .llm import _resolve_openai_base_url

            base_url = _resolve_openai_base_url(
                provider, effective_setting(settings, "llm_base_url")
            )
            raw = _call_openai_vision(
                prompt, image_b64, mime,
                api_key=api_key, model=model, base_url=base_url,
                max_tokens=max_tokens, timeout=timeout,
            )
        data = json.loads(_strip_json_fences(raw))
        extraction = VisionExtraction.model_validate(data)
        extraction.molecules = _verify_molecules(extraction.molecules)
        return extraction, None
    except Exception as exc:
        return None, degrade_return(logger, exc, "vision extraction failed", str(exc)[:200])


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
