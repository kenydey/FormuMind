"""Multi-provider LLM service.

Providers supported:
  anthropic  – Claude (via anthropic SDK)
  openai     – GPT-4o etc. (via openai SDK)
  gemini     – Google Gemini (via google-genai SDK)
  xai        – Grok (openai-compatible, base https://api.x.ai/v1)
  groq       – Meta Llama via Groq (openai-compatible)
  deepseek   – DeepSeek (openai-compatible, base https://api.deepseek.com)
  qwen       – Qwen/DashScope (openai-compatible, base https://dashscope.aliyuncs.com/compatible-mode/v1)
  moonshot   – Kimi (openai-compatible, base https://api.moonshot.cn/v1)
  minimax    – MiniMax (openai-compatible, base https://api.minimax.chat/v1)

All providers fall back to the offline rule-based synthesizer if
the SDK is missing or the API call fails.
"""
from __future__ import annotations

from .errors import degrade_return, optional_import, reraise_if_fatal
import json
import logging
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry as tenacity_retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_settings
from ..services.runtime_secrets import effective_setting
from ..domain.schemas import (
    Evidence,
    ObjectiveSpec,
    ProductDomain,
    RecommendedFormulaListResponse,
    Requirement,
)

# ── Provider metadata ────────────────────────────────────────────────────────
# Used by the settings API to enumerate available options.
PROVIDERS: list[dict] = [
    {
        "id": "anthropic",
        "label": "Anthropic (Claude)",
        "models": [
            {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 (快速)"},
            {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6 (推荐)", "recommended": True},
            {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 (最强)"},
        ],
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "models": [
            {"id": "gpt-4o-mini", "label": "GPT-4o Mini (快速)"},
            {"id": "gpt-4o", "label": "GPT-4o (推荐)", "recommended": True},
            {"id": "o1-mini", "label": "o1-mini (推理)"},
        ],
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "models": [
            {"id": "gemini-1.5-flash", "label": "Gemini 1.5 Flash (快速)"},
            {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash (推荐)", "recommended": True},
            {"id": "gemini-1.5-pro", "label": "Gemini 1.5 Pro"},
        ],
    },
    {
        "id": "xai",
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "models": [
            {"id": "grok-2", "label": "Grok-2 (推荐)", "recommended": True},
            {"id": "grok-2-mini", "label": "Grok-2 Mini (快速)"},
        ],
    },
    {
        "id": "groq",
        "label": "Meta (via Groq)",
        "base_url": "https://api.groq.com/openai/v1",
        "models": [
            {"id": "llama-3.1-8b-instant", "label": "Llama 3.1 8B (极速)"},
            {"id": "llama-3.3-70b-versatile", "label": "Llama 3.3 70B (推荐)", "recommended": True},
        ],
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro (最强)", "recommended": True},
            {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash (快速经济)"},
            {"id": "deepseek-v4-flash-vision-exp", "label": "DeepSeek V4 Flash Vision (视觉·实验)"},
        ],
    },
    {
        "id": "qwen",
        "label": "Qwen 通义千问 (阿里云百炼)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen3.8-max", "label": "Qwen3.8 Max (最强·支持视觉)", "recommended": True},
            {"id": "qwen3.7-max", "label": "Qwen3.7 Max"},
            {"id": "qwen3.7-plus", "label": "Qwen3.7 Plus (性价比·支持视觉)"},
            {"id": "qwen3.6-flash", "label": "Qwen3.6 Flash (快速·支持视觉)"},
        ],
    },
    {
        "id": "moonshot",
        "label": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        # Verified against GET https://api.moonshot.cn/v1/models: the
        # moonshot-v1-* generation this list used to offer is gone, so every
        # option it presented would have 404'd on selection.
        "models": [
            {"id": "kimi-k3", "label": "Kimi K3 (推荐)", "recommended": True},
            {"id": "kimi-k2.6", "label": "Kimi K2.6"},
            {"id": "kimi-k2.7-code", "label": "Kimi K2.7 Code (代码)"},
            {"id": "kimi-k2.7-code-highspeed", "label": "Kimi K2.7 Code 高速"},
        ],
    },
    {
        "id": "minimax",
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "models": [
            {"id": "abab6.5s-chat", "label": "abab6.5s (推荐)", "recommended": True},
            {"id": "abab5.5-chat", "label": "abab5.5 (快速)"},
        ],
    },
    {
        # Bring-your-own endpoint: HuggingFace Inference Endpoints (TGI or
        # vLLM), a self-hosted vLLM, anything speaking OpenAI's chat API. No
        # base_url default and an empty model catalog on purpose — both are
        # whatever the user deployed. TGI wants the literal placeholder "tgi"
        # while vLLM wants the repo id, so the UI offers a free-text field, and
        # "更新列表 ↻" discovers the right answer via GET {base_url}/models,
        # which both engines expose.
        "id": "custom",
        "label": "OpenAI 兼容自定义端点（HF Endpoints / vLLM / TGI）",
        "base_url": None,
        "models": [],
    },
]

_PROVIDER_INDEX: dict[str, dict] = {p["id"]: p for p in PROVIDERS}

_OPENAI_COMPAT_PROVIDERS = frozenset(
    {"openai", "xai", "groq", "deepseek", "qwen", "moonshot", "minimax", "custom"}
)

_EXCLUDE_MODEL_SUBSTR = (
    "embed",
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "audio",
    "transcribe",
    "realtime",
    "sora",
    "text-embedding",
    "image",
    "omni-moderation",
)


def static_models_for_provider(provider: str) -> list[dict]:
    cached = _load_model_cache().get(provider)
    if cached:
        return [dict(m) for m in cached]
    return [dict(m) for m in _PROVIDER_INDEX.get(provider, {}).get("models") or []]


# ── 远端模型列表本地缓存 ────────────────────────────────────────────────────
# 「更新列表」从远端 /models 拉到的模型列表会持久化到 data/llm_models_cache.json，
# 下次打开设置面板直接展示，而不是每次都要点「更新列表」、其余时间回退硬编码目录。
_MODEL_CACHE_REL = Path("data") / "llm_models_cache.json"


def _load_model_cache() -> dict[str, list[dict]]:
    try:
        if _MODEL_CACHE_REL.exists():
            data = json.loads(_MODEL_CACHE_REL.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        degrade_return(log, exc, "load model cache failed", None)
    return {}


def _save_model_cache(cache: dict[str, list[dict]]) -> None:
    try:
        _MODEL_CACHE_REL.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_CACHE_REL.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        degrade_return(log, exc, "save model cache failed", None)


def _persist_provider_models(provider: str, models: list[dict]) -> None:
    cache = _load_model_cache()
    cache[provider] = [dict(m) for m in models]
    _save_model_cache(cache)


def providers_with_cache() -> list[dict]:
    """PROVIDERS 目录，模型列表被持久化的远端列表覆盖（如有）。"""
    cache = _load_model_cache()
    out: list[dict] = []
    for p in PROVIDERS:
        cached = cache.get(p["id"])
        if cached:
            merged = dict(p)
            merged["models"] = [dict(m) for m in cached]
            out.append(merged)
        else:
            out.append(p)
    return out


def _is_listable_chat_model(model_id: str) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid or mid in {"models", "model"}:
        return False
    return not any(token in mid for token in _EXCLUDE_MODEL_SUBSTR)


def _merge_model_catalog(
    static: list[dict],
    remote_ids: list[str],
    current_model: str | None,
) -> list[dict]:
    """Merge remote model ids with the static catalog.

    When the remote endpoint answered, its ids ARE the authoritative model
    names — the static catalog's friendly labels are hardcoded guesses that
    drift, so showing them after a successful refresh defeats the point of
    asking the vendor. A remote hit therefore uses the id as its own label;
    the static catalog (with its labels) is only the fallback for when the
    remote call returned nothing.
    """
    if remote_ids:
        merged: list[dict] = []
        seen: set[str] = set()
        for mid in remote_ids:
            if mid in seen:
                continue
            seen.add(mid)
            merged.append({"id": mid, "label": mid})
    else:
        merged = [dict(m) for m in static]
        seen = {m["id"] for m in merged}
    if current_model and current_model not in seen:
        merged.insert(0, {"id": current_model, "label": current_model})
    for item in merged:
        item.pop("recommended", None)
    rec_id = current_model if current_model and any(m["id"] == current_model for m in merged) else (
        merged[0]["id"] if merged else None
    )
    if rec_id:
        for item in merged:
            if item["id"] == rec_id:
                item["recommended"] = True
                break
    return merged


def fetch_openai_compatible_model_ids(
    base_url: str,
    api_key: str,
    *,
    timeout: float = 30.0,
) -> list[str]:
    import httpx

    root = (base_url or "").strip().rstrip("/") or "https://api.openai.com/v1"
    url = f"{root}/models"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        payload = resp.json()
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for item in items:
        mid = item.get("id") if isinstance(item, dict) else str(item)
        if mid and _is_listable_chat_model(str(mid)):
            ids.append(str(mid))
    return sorted(set(ids))


def list_remote_models(
    provider: str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    current_model: str | None = None,
) -> dict:
    """Fetch provider model catalog from remote API when supported."""
    settings = get_settings()
    provider = str(provider or effective_setting(settings, "llm_provider"))
    current_model = current_model or str(effective_setting(settings, "llm_model"))
    static = static_models_for_provider(provider)
    effective_base = _resolve_openai_base_url(
        provider,
        base_url if base_url is not None else effective_setting(settings, "llm_base_url"),
    )
    key = api_key or settings.get_active_api_key()

    if provider == "anthropic":
        return {
            "ok": True,
            "provider": provider,
            "base_url": None,
            "source": "static",
            "models": _merge_model_catalog(static, [], current_model),
            "message": "Anthropic 暂无 OpenAI 兼容 models 列表，已使用内置目录",
        }

    if provider == "gemini":
        return {
            "ok": True,
            "provider": provider,
            "base_url": None,
            "source": "static",
            "models": _merge_model_catalog(static, [], current_model),
            "message": "Gemini 请使用内置模型目录（远端 list 暂未接入）",
        }

    if not key:
        return {
            "ok": False,
            "provider": provider,
            "base_url": effective_base,
            "source": "static",
            "models": _merge_model_catalog(static, [], current_model),
            "message": f"未配置 {provider} 的 API Key，无法从远端刷新",
        }

    if provider not in _OPENAI_COMPAT_PROVIDERS and not effective_base:
        return {
            "ok": False,
            "provider": provider,
            "base_url": effective_base,
            "source": "static",
            "models": _merge_model_catalog(static, [], current_model),
            "message": "当前供应商不支持远端 models 列表，已回退内置目录",
        }

    try:
        remote_ids = fetch_openai_compatible_model_ids(
            effective_base or "https://api.openai.com/v1",
            key,
            timeout=float(settings.llm_timeout_seconds),
        )
        if not remote_ids:
            return {
                "ok": False,
                "provider": provider,
                "base_url": effective_base,
                "source": "static",
                "models": _merge_model_catalog(static, [], current_model),
                "message": "远端未返回可用 chat 模型，已回退内置目录",
            }
        merged = _merge_model_catalog(static, remote_ids, current_model)
        _persist_provider_models(provider, merged)
        return {
            "ok": True,
            "provider": provider,
            "base_url": effective_base,
            "source": "remote",
            "models": merged,
            "message": f"已从远端加载 {len(remote_ids)} 个模型，并保存到本地",
        }
    except Exception as exc:
        degrade_return(log, exc, "list_remote_models failed", None)
        return {
            "ok": False,
            "provider": provider,
            "base_url": effective_base,
            "source": "static",
            "models": _merge_model_catalog(static, [], current_model),
            "message": f"远端模型列表获取失败：{str(exc)[:200]}",
        }


def _provider_default_base_url(provider: str) -> str | None:
    """Return the catalog default base URL for OpenAI-compatible providers."""
    return _PROVIDER_INDEX.get(provider, {}).get("base_url")


def _resolve_openai_base_url(provider: str, override: str | None) -> str | None:
    """Pick the effective base URL and normalise empty strings."""
    url = (override or "").strip() or _provider_default_base_url(provider)
    return url or None


def _is_deepseek_model(model: str) -> bool:
    """Whether the model id belongs to DeepSeek's thinking-capable family."""
    return model.lower().startswith("deepseek-")


def _openai_message_text(message) -> str | None:
    """Extract assistant text from OpenAI-compatible responses.

    DeepSeek V4 thinking models may place chain-of-thought in ``reasoning_content``
    while leaving ``content`` empty, especially when ``max_tokens`` is small.
    """
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content

    reasoning = getattr(message, "reasoning_content", None)
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    # Some SDK versions stash provider-specific fields in model_extra.
    extra = getattr(message, "model_extra", None) or {}
    reasoning = extra.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    return None


log = logging.getLogger(__name__)
TModel = TypeVar("TModel", bound=BaseModel)


class LLMTransientError(Exception):
    """Network / timeout / rate-limit — safe to retry."""


class LLMValidationError(Exception):
    """Response received but JSON/schema validation failed."""


class LLMConfigError(Exception):
    """Missing SDK or invalid configuration — do not retry."""


# ── Low-level completion helpers ─────────────────────────────────────────────

def _llm_timeout_seconds() -> float:
    return float(get_settings().llm_timeout_seconds)


_LLM_RETRY = tenacity_retry(
    # 2026-09-04: 3 次重试 × 120s 超时让 chat 单步最多挂 6 分钟(deepseek
    # 慢窗口实测 150s+ 无响应)——改为 2 次尝试 + 60s idle 超时(上游持续
    # 吐数据不受影响), 失败更快触发上层降级(offline/无 claims), 问答不无限挂。
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((LLMTransientError, TimeoutError, ConnectionError)),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)


def _is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("401", "403", "authentication", "invalid api key", "unauthorized"))


def _anthropic_request(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise LLMConfigError("未安装 anthropic SDK") from exc
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=_llm_timeout_seconds())
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if not msg.content:
            raise LLMValidationError("Anthropic returned empty content")
        first = msg.content[0]
        if not hasattr(first, "text"):
            raise LLMValidationError(f"Unexpected content block type: {type(first).__name__}")
        text = first.text
        if not text:
            raise LLMValidationError("Anthropic API 返回空响应")
        return text
    except LLMConfigError:
        raise
    except Exception as exc:
        reraise_if_fatal(exc)
        if _is_auth_error(exc):
            raise LLMConfigError(str(exc)) from exc
        raise LLMTransientError(str(exc)) from exc


@_LLM_RETRY
def _complete_anthropic_raw(prompt: str, api_key: str, model: str, max_tokens: int) -> str:
    return _anthropic_request(prompt, api_key, model, max_tokens)


def _complete_anthropic(prompt: str, api_key: str, model: str, max_tokens: int) -> str | None:
    try:
        return _complete_anthropic_raw(prompt, api_key, model, max_tokens)
    except (LLMConfigError, LLMTransientError):
        return None


def _complete_openai_compatible(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None = None,
    *,
    disable_thinking: bool = False,
) -> str | None:
    text, _ = _complete_openai_compatible_detail(
        prompt, api_key, model, max_tokens, base_url, disable_thinking=disable_thinking
    )
    return text


def _openai_compatible_request(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None = None,
    *,
    probe: bool = False,
    disable_thinking: bool = False,
) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise LLMConfigError("未安装 openai SDK，请执行 pip install -e '.[llm]'") from exc
    try:
        kwargs: dict = {"api_key": api_key, "timeout": _llm_timeout_seconds()}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        create_kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        # deepseek v4 系列默认带 thinking: 复杂任务(长 KB 上下文)会把推理草稿
        # 直接写进 content 且拖满 token 预算(实测 154s 草稿污染, 答案被截断)。
        # 问答/探测路径显式关闭 thinking — 输出直接干净(实测 6s)。
        if (probe or disable_thinking) and _is_deepseek_model(model):
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**create_kwargs)
        text = _openai_message_text(resp.choices[0].message)
        if not text:
            raise LLMTransientError("API 返回空响应")
        return text
    except LLMConfigError:
        raise
    except Exception as exc:
        reraise_if_fatal(exc)
        if _is_auth_error(exc):
            raise LLMConfigError(str(exc)) from exc
        raise LLMTransientError(str(exc)) from exc


@_LLM_RETRY
def _complete_openai_compatible_raw(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None = None,
    *,
    probe: bool = False,
    disable_thinking: bool = False,
) -> str:
    return _openai_compatible_request(
        prompt, api_key, model, max_tokens, base_url,
        probe=probe, disable_thinking=disable_thinking,
    )


def _complete_openai_compatible_detail(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None = None,
    *,
    probe: bool = False,
    disable_thinking: bool = False,
) -> tuple[str | None, str | None]:
    """Call an OpenAI-compatible chat API; return (text, error_message)."""
    try:
        text = _complete_openai_compatible_raw(
            prompt, api_key, model, max_tokens, base_url,
            probe=probe, disable_thinking=disable_thinking,
        )
        return text, None
    except LLMConfigError as exc:
        return None, str(exc)
    except LLMTransientError as exc:
        return None, str(exc)


def _gemini_request(prompt: str, api_key: str, model: str) -> str:
    try:
        import google.generativeai as genai  # type: ignore
    except ImportError as exc:
        raise LLMConfigError("未安装 google-generativeai SDK") from exc
    try:
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(model)
        resp = m.generate_content(prompt)
        text = resp.text
        if not text:
            raise LLMTransientError("Gemini API 返回空响应")
        return text
    except LLMConfigError:
        raise
    except Exception as exc:
        reraise_if_fatal(exc)
        if _is_auth_error(exc):
            raise LLMConfigError(str(exc)) from exc
        raise LLMTransientError(str(exc)) from exc


@_LLM_RETRY
def _complete_gemini_raw(prompt: str, api_key: str, model: str) -> str:
    return _gemini_request(prompt, api_key, model)


def _complete_gemini(prompt: str, api_key: str, model: str) -> str | None:
    try:
        return _complete_gemini_raw(prompt, api_key, model)
    except (LLMConfigError, LLMTransientError):
        return None


def _call_llm(
    prompt: str, max_tokens: int | None = None, *, disable_thinking: bool = False
) -> str | None:
    """Route to the configured provider; return None on any failure.

    max_tokens=None → settings.llm_max_tokens (16384 默认, 为长文转录
    设计); 问答类调用应传较小的预算(如 2048), 否则推理模型会拖满
    max_tokens 才停, 单次回答耗时 2 分钟+(2026-09-04 排查 136-143s 根因)。
    disable_thinking=True → deepseek v4 系关闭 thinking(问答主回答用:
    实测 154s 草稿污染 vs 6s 直接干净回答)。
    """
    settings = get_settings()
    provider = effective_setting(settings, "llm_provider")
    api_key = settings.get_active_api_key()
    if not api_key:
        return None
    model = effective_setting(settings, "llm_model")
    max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens

    if provider == "anthropic":
        return _complete_anthropic(prompt, api_key, model, max_tokens)
    if provider == "gemini":
        return _complete_gemini(prompt, api_key, model)

    # All other providers are OpenAI-compatible.
    base_url = _resolve_openai_base_url(provider, effective_setting(settings, "llm_base_url"))
    return _complete_openai_compatible(
        prompt, api_key, model, max_tokens, base_url, disable_thinking=disable_thinking
    )


def complete_json(prompt: str) -> dict | None:
    """Call the configured LLM and parse its reply as a JSON object.

    Tolerates ```` ```json ```` markdown fences. Returns None when no LLM is
    configured or the reply is not valid JSON. Shared by the IP-analysis and
    intent-parsing agents so the fence-stripping logic lives in one place.
    """
    import json

    raw = _call_llm(prompt)
    if not raw:
        return None
    text = raw.strip()
    if "```" in text:
        # Take the content of the first fenced block.
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        return degrade_return(log, exc, "operation failed", None)


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if "```" not in text:
        return text
    parts = text.split("```")
    if len(parts) >= 3:
        inner = parts[1]
    else:
        inner = parts[1] if len(parts) > 1 else text
    if inner.startswith("json"):
        inner = inner[4:]
    return inner.strip()


def _validate_structured(raw: str | None, model_type: type[TModel]) -> TModel:
    if not raw:
        raise LLMValidationError("Empty LLM response")
    try:
        import json

        data = json.loads(_strip_json_fences(raw))
        return model_type.model_validate(data)
    except LLMValidationError:
        raise
    except Exception as exc:
        raise LLMValidationError(f"JSON validation failed: {exc}") from exc


class LLMStructuredUnsupported(Exception):
    """The provider does not implement `response_format` json_schema.

    Not a transient failure and not a config error: the request is fine, the
    endpoint just cannot do native structured output. DeepSeek answers
    "This response_format type is unavailable now" with a 400, which the
    generic handler classified as transient — so every structured call was
    retried three times before failing, spending three requests and ~6s on an
    outcome that could never change.
    """


# Providers already observed to reject `response_format` json_schema — cached so
# subsequent structured calls skip the doomed 400 attempt (DeepSeek among them).
_STRUCTURED_UNSUPPORTED_PROVIDERS: set[str] = set()


# Substrings that mean "structured output is not implemented here", as opposed
# to "your request was malformed".
_STRUCTURED_UNSUPPORTED_MARKERS = (
    "response_format",
    "json_schema",
)


def _is_structured_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    if "unavailable" not in message and "not support" not in message and "unknown" not in message:
        return False
    return any(marker in message for marker in _STRUCTURED_UNSUPPORTED_MARKERS)


def _openai_structured_request(
    system: str,
    user: str,
    model_type: type[TModel],
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None,
    schema: dict,
) -> TModel:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise LLMConfigError("未安装 openai SDK，请执行 pip install -e '.[llm]'") from exc
    try:
        kwargs: dict = {"api_key": api_key, "timeout": _llm_timeout_seconds()}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        create_kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": model_type.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
        }
        if _is_deepseek_model(model):
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**create_kwargs)
        text = _openai_message_text(resp.choices[0].message)
        return _validate_structured(text, model_type)
    except (LLMValidationError, LLMConfigError):
        raise
    except Exception as exc:
        reraise_if_fatal(exc)
        if _is_auth_error(exc):
            raise LLMConfigError(str(exc)) from exc
        if _is_structured_unsupported(exc):
            raise LLMStructuredUnsupported(str(exc)) from exc
        raise LLMTransientError(str(exc)) from exc


def _openai_prompt_structured_request(
    system: str,
    user: str,
    model_type: type[TModel],
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None,
    schema: dict,
) -> TModel:
    """Structured output by asking for JSON in the prompt, then validating.

    Exactly what the Anthropic and Gemini paths already do. Reused here for
    OpenAI-compatible providers that accept the chat API but not
    `response_format` — DeepSeek among them — so a provider without native
    structured output degrades to a working extraction rather than to nothing.
    """
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise LLMConfigError("未安装 openai SDK，请执行 pip install -e '.[llm]'") from exc
    try:
        kwargs: dict = {"api_key": api_key, "timeout": _llm_timeout_seconds()}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        create_kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system}\n\nRespond with ONLY valid JSON matching this "
                        f"schema (no markdown, no commentary):\n{schema}"
                    ),
                },
                {"role": "user", "content": user},
            ],
        }
        if _is_deepseek_model(model):
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**create_kwargs)
        return _validate_structured(_openai_message_text(resp.choices[0].message), model_type)
    except (LLMValidationError, LLMConfigError):
        raise
    except Exception as exc:
        reraise_if_fatal(exc)
        if _is_auth_error(exc):
            raise LLMConfigError(str(exc)) from exc
        raise LLMTransientError(str(exc)) from exc


def _invoke_structured_once(
    system: str,
    user: str,
    model_type: type[TModel],
    *,
    provider: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None,
    schema: dict,
) -> TModel:
    if provider not in ("anthropic", "gemini"):
        if provider in _STRUCTURED_UNSUPPORTED_PROVIDERS:
            # 已知该 provider 不支持 native structured output，直接 prompt-based，
            # 省去每次先发一次必然 400 的请求。
            return _openai_prompt_structured_request(
                system,
                user,
                model_type,
                api_key=api_key,
                model=model,
                max_tokens=max_tokens,
                base_url=base_url,
                schema=schema,
            )
        try:
            return _openai_structured_request(
                system,
                user,
                model_type,
                api_key=api_key,
                model=model,
                max_tokens=max_tokens,
                base_url=base_url,
                schema=schema,
            )
        except LLMStructuredUnsupported as exc:
            # Fall back rather than retry: the endpoint will keep saying no.
            _STRUCTURED_UNSUPPORTED_PROVIDERS.add(provider)
            log.info(
                "%s has no native structured output (%s) — using prompt-based JSON",
                provider, exc,
            )
            return _openai_prompt_structured_request(
                system,
                user,
                model_type,
                api_key=api_key,
                model=model,
                max_tokens=max_tokens,
                base_url=base_url,
                schema=schema,
            )

    combined = (
        f"{system}\n\n"
        f"Respond with ONLY valid JSON matching this schema (no markdown):\n"
        f"{schema}\n\n"
        f"{user}"
    )
    if provider == "anthropic":
        raw = _anthropic_request(combined, api_key, model, max_tokens)
    else:
        raw = _gemini_request(combined, api_key, model)
    return _validate_structured(raw, model_type)


def complete_structured(
    system: str,
    user: str,
    model_type: type[TModel],
    *,
    retry: bool = True,
) -> tuple[TModel | None, str | None]:
    """Call LLM and parse response into a Pydantic model."""
    settings = get_settings()
    provider = effective_setting(settings, "llm_provider")
    api_key = settings.get_active_api_key()
    if not api_key:
        return None, "No LLM API key configured"

    model = effective_setting(settings, "llm_model")
    max_tokens = settings.llm_max_tokens
    schema = model_type.model_json_schema()
    base_url = _resolve_openai_base_url(provider, effective_setting(settings, "llm_base_url"))

    structured_retry = tenacity_retry(
        stop=stop_after_attempt(3 if retry else 1),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (LLMTransientError, LLMValidationError, TimeoutError, ConnectionError)
        ),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )

    @structured_retry
    def _run() -> TModel:
        return _invoke_structured_once(
            system,
            user,
            model_type,
            provider=provider,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            base_url=base_url,
            schema=schema,
        )

    try:
        return _run(), None
    except LLMConfigError as exc:
        return None, str(exc)
    except (LLMTransientError, LLMValidationError) as exc:
        log.warning("Structured LLM failed after retries: %s", exc)
        return None, str(exc)
    except Exception as exc:
        log.warning("Structured LLM unexpected error: %s", exc)
        return None, str(exc)


def _objectives_prompt_block(objectives: list[ObjectiveSpec]) -> str:
    lines = []
    for o in objectives:
        bits = [f"metric={o.metric}", f"direction={o.direction}", f"weight={o.weight}"]
        if o.target_value is not None:
            bits.append(f"target={o.target_value}")
        if o.display_name:
            bits.append(f"label={o.display_name}")
        if o.unit:
            bits.append(f"unit={o.unit}")
        if o.ref_min is not None:
            bits.append(f"ref_min={o.ref_min}")
        if o.ref_max is not None:
            bits.append(f"ref_max={o.ref_max}")
        lines.append("- " + ", ".join(bits))
    return "\n".join(lines) if lines else "- (use domain defaults)"


def _levers_prompt_block(req: Requirement) -> str:
    from ..domain.project_spec import resolve_levers

    levers = resolve_levers(req)
    if not levers:
        return ""
    lines = "\n".join(
        f"- {lev.name}: {lev.low}–{lev.high} {lev.unit}" for lev in levers
    )
    return f"\n\n可调 DOE 因子:\n{lines}"


def _base_formulas_prompt_block(base_formulas: list) -> str:
    if not base_formulas:
        return ""
    lines = []
    for form in base_formulas[:5]:
        name = getattr(form, "name", str(form.get("name", "")))
        ings = getattr(form, "ingredients", form.get("ingredients", []))
        parts = []
        for ing in ings[:12]:
            iname = getattr(ing, "name", ing.get("name", ""))
            pct = getattr(ing, "weight_pct", ing.get("weight_pct", ""))
            parts.append(f"{iname} {pct}%")
        lines.append(f"- {name}: " + ", ".join(parts))
    return "\n\n待修改基准配方:\n" + "\n".join(lines)


def _recommend_system_prompt() -> str:
    return (
        "You are a formulation chemist for metal surface treatment (coatings, degreasers, conversion treatments).\n"
        "Design formulations strictly from the provided objectives array.\n"
        "Rules:\n"
        "1. Include cas_no only when confident; leave blank if uncertain — the server catalog will enrich it.\n"
        "2. Include zh_name for every component when the product context is Chinese.\n"
        "3. Include smiles when known; molar_mass when calculable.\n"
        "4. For coating formulations weight_pct values should sum to approximately 100.\n"
        "5. Populate component_type (resin/hardener/inhibitor/solvent/etc), amount_display, and notes in Chinese where helpful.\n"
        "6. objectives_summary explains how the recipe meets each objective.\n"
        "7. Return JSON only — no markdown fences.\n"
        "8. RESPECT product_type strictly: if the product_type/headline says 「含聚合物/树脂的乳液型」 (polymer/resin EMULSION type), you MUST include a polymer resin (acrylic / epoxy / polyurethane emulsion) as the film-forming binder — a purely inorganic conversion coating does NOT satisfy an emulsion-type product.\n"
        "9. Salt-spray realism: purely inorganic conversion coatings (zirconate/silane/rare-earth, 2-5% solids) realistically reach only 50-200h salt spray; only organic polymer/resin emulsion systems reach 500-1440h. Never claim 500h+ for an inorganic-only formula — keep predicted salt_spray_hours consistent with the formula type."
    )


def _constraints_prompt_block(req: Requirement) -> str:
    from ..domain.project_spec import normalize_constraints

    merged = normalize_constraints(req)
    if not merged:
        return "- (none specified)"
    return "\n".join(f"- {k}: {v}" for k, v in merged.items())


def _func_groups_prompt_block(req: Requirement, base_formulas: list | None) -> str:
    """Functional-group summary of known materials (ChemCrow gateway; "" offline).

    Grounds the LLM's structure choices in the actual chemistry of the
    project's materials instead of name-level pattern matching.
    """
    from .chemtools import func_group_summary

    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in req.materials or []:
        if m.smiles and m.name not in seen:
            items.append((m.name, m.smiles))
            seen.add(m.name)
    for f in base_formulas or []:
        for ing in getattr(f, "ingredients", []) or []:
            smiles = getattr(ing, "smiles", None)
            name = getattr(ing, "name", "")
            if smiles and name and name not in seen:
                items.append((name, smiles))
                seen.add(name)
    summary = func_group_summary(items)
    if not summary:
        return ""
    return f"\nKnown material functional groups (ground your chemistry in these):\n{summary}\n"


def _product_hints_prompt_block(req: Requirement) -> str:
    """Corpus-derived commercial grades for the project materials ("" offline).

    Literature/patents ingested into the KB frequently name real trade
    products (Epon 828, Aerosil 200…); surfacing them lets the LLM anchor its
    ingredient list to purchasable grades and known suppliers.
    """
    from .kb_index import product_hints

    lines = product_hints(req.materials or [])
    if not lines:
        return ""
    return (
        "\n文献语料中该体系的常用商业牌号（可在 ingredient 备注中引用，不强制）：\n"
        + "\n".join(lines)
        + "\n"
    )


def _resolve_system_constraints(req: Requirement) -> str:
    """Resolve formulation-system constraints via the 3-tier funnel.

    1. static KB (26 systems + ISO 12944 grades) — authoritative
    2. inferred cache (SQLite) — self-learned, hit_count++
    3. fresh LLM inference — persisted to the cache for reuse
    """
    from ..db.inferred_system_store import get_inferred_system_store
    from ..domain.formulation_systems import (
        build_system_prompt_block,
        normalize_key,
    )

    product_type = req.product_type or ""
    block = build_system_prompt_block(product_type)
    if block:
        return block

    key = normalize_key(product_type)
    if key:
        store = get_inferred_system_store()
        cached = store.match(key)
        if cached is not None:
            log.info("命中沉淀约束: {}", product_type)
            return _format_inferred_block(cached)

        inferred = _infer_system_constraints(product_type)
        if inferred is not None:
            store.upsert(
                key,
                product_type,
                inferred,
                source_requirement_id=_requirement_fingerprint(req),
                source_requirement_text=req.headline(),
            )
            return _format_inferred_block(inferred)

    # 三层都失败 → 软推理指令（保留 P1 行为）
    return _infer_constraints_block()


def _infer_constraints_block() -> str:
    return (
        "Formulation-system requirements (INFER — product_type does not match a known "
        "system; before designing, FIRST infer and state the system constraints from the "
        "headline/substrate, then design strictly within them):\n"
        "- Required components (film-forming resin / active / hardener) and their roles\n"
        "- Forbidden components (e.g. no chromate for chrome-free, no VOC for waterborne)\n"
        "- Process conditions (pH, cure temperature, solids content)\n"
        "- Realistic performance ranges (salt spray / film weight / cost) — be conservative "
        "and physically plausible; do not claim unrealistic values\n"
    )


def _format_inferred_block(sys) -> str:
    lines = [
        "Formulation-system requirements (self-learned — verify before relying):"
    ]
    if sys.system_name:
        lines.append(f"- System: {sys.system_name}")
    if sys.must_include_roles:
        lines.append(f"- Required component roles: {', '.join(sys.must_include_roles)}")
    if sys.must_exclude:
        lines.append(f"- Forbidden: {sys.must_exclude}")
    for c in sys.constraints:
        lines.append(f"- {c}")
    for metric, (lo, hi) in sys.metric_ranges.items():
        lines.append(f"- {metric}: {lo}–{hi}")
    return "\n".join(lines) + "\n"


def _infer_system_constraints(product_type: str):
    from ..domain.schemas import InferredSystem

    system = (
        "You are a formulation-system analyst for industrial chemical R&D.\n"
        "Given a product type, infer its formulation-system constraints.\n"
        "Return JSON only, no markdown fences.\n"
        "Fields:\n"
        "- system_name: short system name\n"
        "- must_include_roles: required component roles (resin/hardener/inhibitor/solvent/...)\n"
        "- must_exclude: forbidden components (e.g. 'no chromate for chrome-free')\n"
        "- constraints: process conditions (pH, cure temperature, solids)\n"
        "- metric_ranges: realistic performance ranges as {metric: [min, max]}, "
        'e.g. {"salt_spray_hours": [500, 1440]}. Be conservative and physically plausible.\n'
        "- confidence: high/medium/low\n"
    )
    user = f"Product type: {product_type}\nInfer its formulation-system constraints."
    parsed, err = complete_structured(system, user, InferredSystem)
    if parsed is not None:
        return parsed
    log.warning("体系约束推理失败: {}", err)
    return None


def _requirement_fingerprint(req: Requirement) -> str:
    import hashlib

    return hashlib.sha1(req.headline().encode("utf-8")).hexdigest()[:16]


def _recommend_user_prompt(
    req: Requirement,
    objectives: list[ObjectiveSpec],
    evidence: list[Evidence],
    n: int,
    *,
    modify_prompt: str = "",
    base_formulas: list | None = None,
    system_block: str = "",
    historical_context: str = "",
) -> str:
    citations = "\n".join(
        f"[{e.source}] {e.title}: {e.snippet[:200]}" for e in evidence[:6]
    ) or "(no external evidence — use domain knowledge)"
    modify_block = ""
    if modify_prompt:
        modify_block = f"\n\n<user_modify_request>\n{modify_prompt.strip()}\n</user_modify_request>"
    return (
        f"Domain: {req.domain.value}\n"
        f"Substrate: {req.substrate.value}\n"
        f"Headline: {req.headline()}\n"
        f"Salt spray target (h): {req.salt_spray_hours}\n"
        f"Cleaning target (%): {req.cleaning_efficiency}\n"
        f"VOC limit (g/L): {req.voc_limit_gpl}\n"
        f"Cure temp (C): {req.cure_temperature_c}\n"
        f"Film weight target (g/m²): {req.film_weight_gsm}\n"
        f"pH target: {req.ph_target}\n"
        f"Notes: {req.notes}\n\n"
        f"Objectives:\n{_objectives_prompt_block(objectives)}\n\n"
        f"Process constraints (must respect):\n{_constraints_prompt_block(req)}\n"
        f"{system_block}"
        f"{_levers_prompt_block(req)}"
        f"{_func_groups_prompt_block(req, base_formulas)}"
        f"{_product_hints_prompt_block(req)}"
        f"{_base_formulas_prompt_block(base_formulas or [])}"
        f"{modify_block}\n\n"
        f"Evidence:\n{citations}\n\n"
        f"Produce exactly {n} distinct recommended formulas in the formulas array."
    )


def recommend_formulations(
    req: Requirement,
    objectives: list[ObjectiveSpec] | None = None,
    evidence: list[Evidence] | None = None,
    *,
    n: int = 3,
    modify_prompt: str = "",
    base_formulas: list | None = None,
) -> RecommendedFormulaListResponse:
    """Primary recommend engine: LLM structured JSON with offline fallback."""
    from ..domain.formulation_gate import offline_recommend_response, validate_recommended_formulas
    from ..domain.knowledge import offline_recommend_fallback
    from ..domain.objective_contract import normalize_objectives

    objectives = objectives or normalize_objectives(req)
    evidence = evidence or []

    # Inject historical similar formulations query
    historical_context = ""
    try:
        from ..services.kg.formulation_similarity import find_similar_formulations
        from ..db.database import default_session_factory
        from ..db.models import ExperimentRow
        factory = default_session_factory()
        with factory() as session:
            rows = session.query(ExperimentRow).all()
            all_exps = [
                {"id": r.id, "project_id": r.project_id or "", "domain": r.domain or "", "factors": r.factors or {}, "measured": r.measured or {}}
                for r in rows
            ]
        # Build a lightweight factor dict from requirement materials
        query_factors = {m.name: float(m.weight_pct or 0) for m in (req.materials or []) if m.name and m.weight_pct}
        if query_factors:
            sims = find_similar_formulations(query_factors, all_exps, domain=req.domain.value, limit=5)
            if sims:
                lines = []
                for s in sims[:3]:
                    factors_str = ", ".join(f"{k}:{v}%" for k, v in list(s.get("factors", {}).items())[:4])
                    measured_str = ", ".join(f"{k}={v}" for k, v in list(s.get("measured", {}).items())[:3])
                    lines.append(f"- Exp #{s['experiment_id']} (sim={s['similarity']:.0%}): {factors_str} | measured: {measured_str}")
                historical_context = "\\n".join(lines)
    except Exception as exc:
        log.debug("Historical similarity query failed (non-fatal): %s", exc)

    system = _recommend_system_prompt()
    system_block = _resolve_system_constraints(req)
    user = _recommend_user_prompt(
        req, objectives, evidence, n,
        modify_prompt=modify_prompt,
        base_formulas=base_formulas,
        system_block=system_block,
        historical_context=historical_context,
    )

    parsed, err = complete_structured(system, user, RecommendedFormulaListResponse)
    if parsed and parsed.formulas:
        normalized = [
            f.model_copy(update={"domain": req.domain, "engine": "llm"})
            for f in parsed.formulas[:n]
        ]
        formulas, val_warnings = validate_recommended_formulas(normalized)
        return RecommendedFormulaListResponse(
            formulas=formulas,
            warnings=val_warnings + parsed.warnings,
            engine="llm",
        )

    reason = err or "LLM structured recommend failed"
    log.info("Falling back to offline recommend: %s", reason)
    offline_forms = offline_recommend_fallback(req, n=n)
    return offline_recommend_response(offline_forms, reason=reason)


# ── Prompt builders ──────────────────────────────────────────────────────────

def _evidence_prompt(req: Requirement, evidence: list[Evidence], recommended: list) -> str:
    citations = "\n".join(
        f"[{e.source}] {e.title}: {e.snippet[:300]}" for e in evidence[:6]
    )
    recs = "\n".join(
        f"- {f.name}: {', '.join(i.name for i in f.ingredients[:4])}" for f in recommended[:3]
    )
    return (
        f"You are a formulation chemist specializing in metal surface treatment.\n"
        f"Domain: {req.domain.value}\nSubstrate: {req.substrate.value}\n"
        f"Cure temperature ≤ {req.cure_temperature_c}°C, VOC ≤ {req.voc_limit_gpl} g/L\n\n"
        f"Evidence from patents/literature:\n{citations}\n\n"
        f"Candidate formulations:\n{recs}\n\n"
        f"Summarise the reaction mechanism and explain why the top candidate is recommended. "
        f"Be concise (≤ 200 words). Reply in the same language as the domain context (Chinese preferred)."
    )


def _build_context(evidence: list[Evidence], *, max_chars: int | None = None) -> str:
    """Join full evidence snippets into the LLM context, budget-capped.

    The old prompt truncated each snippet to 400 chars and kept only 8 items,
    so the model saw at most ~3.2K chars of abstract-level text. Full chunks
    (up to ``chat_context_max_chars``) let it synthesise across documents the
    way NotebookLM does — the rerank stage already narrowed to the most relevant.
    """
    if max_chars is None:
        from ..config import get_settings

        max_chars = get_settings().chat_context_max_chars
    parts: list[str] = []
    total = 0
    for i, e in enumerate(evidence):
        snippet = (e.snippet or "").strip()
        if not snippet:
            continue
        line = f"[{i+1}] ({e.source}) {e.title}: {snippet}"
        if total + len(line) > max_chars:
            room = max_chars - total
            if room > 120:
                parts.append(line[:room])
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts)


def _chat_prompt(
    question: str,
    evidence: list[Evidence],
    domain: str | None,
    *,
    history: list | None = None,
    structure: dict | None = None,
) -> str:
    context = _build_context(evidence)
    domain_hint = f"Domain context: {domain}\n" if domain else ""
    hist_block = ""
    if history:
        lines = []
        for turn in history[-4:]:
            role = getattr(turn, "role", None) or (turn.get("role") if isinstance(turn, dict) else "")
            content = getattr(turn, "content", None) or (turn.get("content") if isinstance(turn, dict) else "")
            if content:
                lines.append(f"{role}: {str(content)[:400]}")
        if lines:
            hist_block = "Recent dialogue:\n" + "\n".join(lines) + "\n\n"
    # P0: 结构图识别结果铺进推理上下文 — MolJSON 显式原子/键让 LLM
    # 数碳/环/官能团零误差（P0 benchmark +7pp 的落地）。
    # M-A: 附 RDKit 算好的 meta 摘要（分子式/分子量/环数），LLM 直接引用
    # 无需自行计数。
    struct_block = ""
    if structure:
        try:
            from .moljson import validate_smiles, smiles_to_moljson, moljson_meta, detect_functional_groups

            smiles = (structure.get("smiles") or "").strip()
            info = validate_smiles(smiles) if smiles else {"valid": False}
            if info.get("valid"):
                mj = smiles_to_moljson(smiles)
                if mj:
                    import json as _json

                    meta = moljson_meta(smiles)
                    if meta:
                        meta_json = _json.dumps(meta, ensure_ascii=False)
                        meta_line = (
                            "\nComputed properties (authoritative, do not re-count):\n"
                            f"{meta_json}\n"
                        )
                    else:
                        meta_line = ""
                    # M-C: 官能团检测摘要（SMARTS 命中列表）
                    groups = detect_functional_groups(smiles)
                    groups_line = (
                        f"Functional groups detected by SMARTS: {', '.join(groups)}\n"
                        if groups
                        else ""
                    )
                    struct_block = (
                        "Target molecular structure (authoritative graph, use this "
                        "over any textual hints when counting atoms/rings/functional groups):\n"
                        f"```json\n{_json.dumps(mj, ensure_ascii=False)}\n```\n"
                        f"{meta_line}"
                        f"{groups_line}\n"
                    )
        except Exception as exc:
            log.debug("structure prompt block failed: %s", exc)
    trade_suffix = ""
    try:
        from .kg.retrieval import trade_product_prompt_suffix

        trade_suffix = trade_product_prompt_suffix(evidence)
    except Exception as exc:
        log.debug("trade product prompt suffix unavailable: %s", exc)
    return (
        f"You are a formulation chemist. Answer the question using ONLY the provided sources. "
        f"Cite sources by number [1], [2], etc.\n"
        f"Chemistry notation rules: keep reaction equations as LaTeX inside $$…$$; "
        f"keep molecular formulas as plain text with digits (Zn3(PO4)2); when giving a "
        f"molecular structure, put its SMILES in a fenced code block tagged `smiles`; "
        f"keep Markdown tables intact; preserve commercial trade names / grades / "
        f"suppliers exactly as the sources write them.\n"
        f"{domain_hint}"
        f"{hist_block}"
        f"{struct_block}"
        f"Sources:\n{context}\n\n"
        f"<user_question>\n{question}\n</user_question>\n\n"
        f"Answer concisely in the same language as the question (Markdown allowed):"
        f"{trade_suffix}"
    )


# ── Offline fallback ─────────────────────────────────────────────────────────

_DRAFT_MARKERS = (
    "let's ", "let me ", "perhaps ", "need to answer", "need answer",
    "we need answer", "i'll formulate", "i'll write", "let's examine",
    "let us ", "we should ", "i think i ", "formulate:", "draft:",
    "need to check", "let's look", "not sure", "maybe combine",
)


def _looks_like_draft(text: str | None) -> bool:
    """草稿污染检测: LLM 把推理过程(中英混杂自言自语)写进 content 的特征。

    2026-09-04 实测: deepseek v4 默认 thinking 在长 KB 上下文下输出
    "我们 need answer Chinese… Let's examine… Perhaps combine…" 这类草稿,
    2048 token 预算被耗尽, 正式答案被截断。命中特征词 ≥2 且文本偏长 → 判草稿。
    """
    if not text:
        return False
    t = text.lower()
    hits = sum(1 for m in _DRAFT_MARKERS if m in t)
    return hits >= 2 and len(text) > 600


def _call_with_deadline(fn: Callable[[], str | None], seconds: float) -> str | None:
    """Run *fn* on a worker thread with a hard wall-clock deadline.

    2026-09-04: deepseek 慢窗口下 openai SDK 的 idle 超时(60s)不覆盖
    "持续慢速吐数据"场景, 单次 LLM 调用实测挂 150s+。问答主回答用它
    封顶 45s, 超时返回 None → 上层降级 offline snippet, 请求必有响应。
    孤儿线程 shutdown(wait=False) 自然结束, 不阻塞后续请求。
    """
    import concurrent.futures

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=seconds)
    except Exception:
        return None
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _offline_synthesis(req: Requirement, evidence: list[Evidence], recommended: list) -> tuple[str, str]:
    """Deterministic rule-based synthesis — works without any API key."""
    domain_names = {
        ProductDomain.anticorrosion_coating: "防腐蚀涂料",
        ProductDomain.degreaser: "脱脂剂",
        ProductDomain.surface_treatment: "表面处理剂",
        ProductDomain.autodeposition_coating: "自沉积涂料",
    }
    d = domain_names.get(req.domain, req.domain.value)
    top = recommended[0] if recommended else None
    mech = (
        f"{d}的核心机理：{'环氧树脂与固化剂形成交联网络，缓蚀剂（磷酸锌等）在界面形成致密保护膜，阻断腐蚀电化学反应。' if req.domain == ProductDomain.anticorrosion_coating else '表面活性剂降低油-水界面张力，使油污乳化脱落；碱性助剂（磷酸钠、碳酸钠）皂化动植物油脂。' if req.domain == ProductDomain.degreaser else '磷化/铬化/硅烷偶联形成转化膜，提升基材与后续涂层的附着力与耐蚀性。' if req.domain == ProductDomain.surface_treatment else '酸性浴中聚合物分散体在金属表面酸致凝聚沉积：HF 刻蚀基材溶出铁离子，Fe³⁺（FeF3）与氧化剂（H2O2）维持界面凝聚驱动力，涂层在 pH 2-4 浴中自沉积生长。'}"
    )
    chat = f"## {d} 配方研究报告\n\n**机理**：{mech}\n\n"
    if top:
        chat += f"**推荐配方**：{top.name}，预测耐盐雾 {top.predicted.get('salt_spray_hours', '—')} h，成本 {top.predicted.get('cost_cny_per_kg', '—')} CNY/kg。\n"
    if evidence:
        chat += f"\n**检索到 {len(evidence)} 条参考文献**，相关度最高：{evidence[0].title}。"
    return mech, chat


# ── Backward-compatible helpers used by existing pipeline ────────────────────

def _legacy_offline_narrative(req: Requirement, evidence: list[Evidence], recommended: list) -> str:
    """Re-create the original deterministic markdown narrative for the pipeline."""
    from ..domain.knowledge import MECHANISMS
    mechanism = MECHANISMS[req.domain]
    lines = [
        f"### Research summary — {req.headline()}",
        "",
        "**Retrieved prior art:**",
    ]
    for e in evidence:
        lines.append(f"- `{e.identifier}` ({e.source}) — {e.title}. {e.snippet}")
    lines += ["", "**Protection / cleaning mechanism:**", mechanism, "", "**Candidate formulations:**"]
    for i, f in enumerate(recommended, start=1):
        comp = ", ".join(f"{ing.name} {ing.weight_pct}%" for ing in f.ingredients)
        lines.append(f"{i}. **{f.name}** — {comp}")
        if f.predicted:
            preds = ", ".join(f"{k}={v}" for k, v in f.predicted.items())
            lines.append(f"   - predicted: {preds}")
    lines += [
        "",
        "_Next: generate a DOE plan on the key levers, then run the closed-loop optimizer to rank the top candidates._",
    ]
    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────────────

def synthesize_research(
    req: Requirement,
    evidence: list[Evidence],
    recommended: list,
) -> tuple[str, str]:
    """Return (mechanism_text, chat_markdown). Falls back offline if LLM unavailable.

    Backward-compatible with the existing pipeline (accepts Formulation list).
    """
    from ..domain.knowledge import MECHANISMS
    mechanism = MECHANISMS[req.domain]

    prompt = _evidence_prompt(req, evidence, recommended)
    result = _call_llm(prompt)
    if result:
        return mechanism, result

    # Original deterministic offline narrative (preserves existing test behaviour).
    return mechanism, _legacy_offline_narrative(req, evidence, recommended)


# ── Optional knowledge-agent adapters (best-effort, with fallback) ───────────
# These upgrade the grounded-Q&A path when the corresponding optional library
# is installed and configured. Each is gated behind an availability probe and a
# try/except so the default TF-IDF + multi-LLM path (and the offline fallback)
# are never affected when the library is absent or its API has drifted.
#
# ChemCrow's ReAct adapter was removed 2026-09 (docs/plans/2026-09-04-dechemcrow.md):
# it required an OpenAI-family key the DeepSeek-only deployment never had, so
# chemistry questions now flow straight to the RAG + multi-LLM tiers below.


def _paperqa_available() -> bool:
    if not optional_import("paperqa"):
        return False
    settings = get_settings()
    # paper-qa 默认走 OpenAI（llm=gpt-4o + embedding=text-embedding-3-small），
    # 二者都需要 OPENAI key。DeepSeek-only 环境（本项目）没有 OPENAI key，
    # 直接跳过 Tier 2，避免 litellm 每次空跑 3 次重试并报 "Missing credentials"
    # （随后才 fall through 到 Tier 3）。配置了 OPENAI key 时自动恢复该路径。
    return bool(effective_setting(settings, "openai_api_key"))


async def _paperqa_answer(
    question: str, sources: list[Evidence]
) -> tuple[str, list[Evidence]] | None:
    """Answer via paper-qa's semantic retrieval + cited synthesis."""
    try:  # pragma: no cover - requires paper-qa + embeddings/LLM
        from paperqa import Docs, Doc, Text

        docs = Docs()
        by_key: dict[str, Evidence] = {}
        for i, ev in enumerate(sources):
            text = f"{ev.title}. {ev.snippet}".strip()
            if not text:
                continue
            key = ev.identifier or ev.title or str(i)
            doc = Doc(docname=key, citation=ev.source, dockey=str(i))
            await docs.aadd_texts([Text(text=text, name=key, doc=doc)], doc)
            by_key[key] = ev
        answer = await docs.aquery(question)
        text = getattr(answer, "answer", None) or str(answer)
        cited = [by_key[k] for k in by_key if k in (getattr(answer, "context", "") or "")]
        return text, (cited or sources[:6])
    except Exception as exc:
        return degrade_return(log, exc, "operation failed", None)


def _run_paperqa(
    question: str, sources: list[Evidence]
) -> tuple[str, list[Evidence]] | None:
    """Run the async paper-qa tier from the sync ``answer_question``.

    In a plain sync context (tests, research_graph, deep_research engine) there is
    no running loop, so ``asyncio.run`` is safe. Inside an async endpoint
    (chat.py) a loop is already running and ``asyncio.run`` would raise — paper-qa
    is a best-effort tier there, so return None and let the caller fall through to
    the LLM tier (Tier 3).
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_paperqa_answer(question, sources))
    return None


def answer_question(
    question: str,
    sources: list[Evidence],
    domain: str | None = None,
    *,
    history: list | None = None,
    structure: dict | None = None,
) -> tuple[str, list[Evidence]]:
    """Answer a user question grounded in the provided sources.

    Routing (each tier degrades gracefully to the next):
      1. paper-qa — semantic retrieval + cited synthesis, when installed.
      2. TF-IDF re-rank → configured multi-LLM provider.
      3. Offline: the most relevant retrieved snippet.

    (The former ChemCrow ReAct tier was removed 2026-09; it required an
    OpenAI-family key this DeepSeek-only deployment never had.)

    Returns (answer_text, cited_sources).
    """
    from ..services.rag import build_store

    settings = get_settings()

    # 召回（粗排）→ LLM 精排（无 GPU 时 LLM rerank 替代 ColBERT 语义排序）。
    store = build_store()
    store.ingest(sources)
    candidates_n = min(settings.chat_rerank_candidates, max(1, len(sources)))
    recalled = store.query(question, k=candidates_n) or sources[:candidates_n]

    # 2026-09-04: chat 主路径不做 LLM 二次精排。kb_augment(图谱/KB)与
    # BM25 已两级排序, llm_rerank 再对 50 条候选打分在 deepseek 慢窗口
    # 实测多花 30-76s/问(耗时探针 answer=93.5s 的大头), 收益边际。
    # 深度研究/文献检索等长任务路径的 llm_rerank 不受影响。
    relevant = recalled[: settings.chat_rerank_top_k]

    # Tier 2: paper-qa semantic synthesis with citations.
    if _paperqa_available() and sources:
        pq = _run_paperqa(question, sources)
        if pq:
            return pq

    # Tier 3: configured multi-LLM provider over re-ranked sources.
    prompt = _chat_prompt(question, relevant, domain, history=history, structure=structure)
    # 问答主回答: 小 token 预算 + 关闭 thinking(2026-09-04 深度排查):
    # 1) 16384 预算会让推理模型拖满, 单次 2 分钟+(136-143s 慢响应根因);
    # 2) deepseek v4 默认 thinking 在长 KB 上下文下把推理草稿写进 content
    #    (中英混杂 "Let's examine…"), 2048 预算被草稿耗尽 → 正式答案被截断,
    #    前端表现为"答非所问/报错"。关闭后直接输出干净答案(实测 6s vs 154s)。
    answer = _call_with_deadline(
        lambda: _call_llm(prompt, max_tokens=2048, disable_thinking=True), 45.0
    )
    if _looks_like_draft(answer):
        log.warning("answer_question: LLM 输出疑似推理草稿(%d 字符), 重试一次", len(answer or ""))
        retry = _call_with_deadline(
            lambda: _call_llm(prompt, max_tokens=2048, disable_thinking=True), 45.0
        )
        answer = retry or answer
    if not answer:
        # Tier 4 — offline fallback: return the most relevant snippet.
        if relevant:
            answer = f"根据已加载资料：{relevant[0].snippet[:300]}…"
        else:
            answer = "暂无相关资料，请先检索或上传文献。"
    return answer, relevant


def test_connection() -> dict:
    """Test the current LLM configuration. Returns {ok, provider, model, message}."""
    settings = get_settings()
    provider = effective_setting(settings, "llm_provider")
    api_key = settings.get_active_api_key()
    model = effective_setting(settings, "llm_model")
    if not api_key:
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "message": f"未配置 {provider} 的 API Key",
        }

    prompt = "Reply with exactly: OK"
    if provider == "anthropic":
        result = _complete_anthropic(prompt, api_key, model, min(settings.llm_max_tokens, 16))
        error = None if result else "Anthropic API 调用失败，请检查 API Key 和网络"
    elif provider == "gemini":
        result = _complete_gemini(prompt, api_key, model)
        error = None if result else "Gemini API 调用失败，请检查 API Key 和网络"
    else:
        base_url = _resolve_openai_base_url(provider, effective_setting(settings, "llm_base_url"))
        result, error = _complete_openai_compatible_detail(
            prompt,
            api_key,
            model,
            min(settings.llm_max_tokens, 64),
            base_url,
            probe=True,
        )

    if result and "ok" in result.lower():
        return {"ok": True, "provider": provider, "model": model, "message": "连接成功"}
    if result:
        return {"ok": True, "provider": provider, "model": model, "message": "连接成功（响应异常）"}
    detail = error or "API 调用失败，请检查 API Key 和网络"
    if "Authentication" in detail or "401" in detail or "invalid" in detail.lower():
        detail = "API Key 无效或已过期，请检查密钥是否正确"
    elif "model" in detail.lower() and ("not found" in detail.lower() or "does not exist" in detail.lower()):
        detail = f"模型 {model} 不存在或当前账户无权限，请更换模型后重试"
    return {"ok": False, "provider": provider, "model": model, "message": detail}
