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

import logging
from typing import TypeVar

from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry as tenacity_retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_settings
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
        ],
    },
    {
        "id": "qwen",
        "label": "Qwen (通义千问)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [
            {"id": "qwen-turbo", "label": "Qwen Turbo (快速)"},
            {"id": "qwen-plus", "label": "Qwen Plus (推荐)", "recommended": True},
            {"id": "qwen-max", "label": "Qwen Max (最强)"},
        ],
    },
    {
        "id": "moonshot",
        "label": "Kimi (Moonshot)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": [
            {"id": "moonshot-v1-8k", "label": "Moonshot 8K (极速)"},
            {"id": "moonshot-v1-32k", "label": "Moonshot 32K"},
            {"id": "moonshot-v1-128k", "label": "Moonshot 128K (推荐)", "recommended": True},
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
]

_PROVIDER_INDEX: dict[str, dict] = {p["id"]: p for p in PROVIDERS}


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
    stop=stop_after_attempt(3),
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
        text = msg.content[0].text
        if not text:
            raise LLMTransientError("Anthropic API 返回空响应")
        return text
    except LLMConfigError:
        raise
    except Exception as exc:
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
    prompt: str, api_key: str, model: str, max_tokens: int, base_url: str | None = None
) -> str | None:
    text, _ = _complete_openai_compatible_detail(prompt, api_key, model, max_tokens, base_url)
    return text


def _openai_compatible_request(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None = None,
    *,
    probe: bool = False,
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
        if probe and _is_deepseek_model(model):
            create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**create_kwargs)
        text = _openai_message_text(resp.choices[0].message)
        if not text:
            raise LLMTransientError("API 返回空响应")
        return text
    except LLMConfigError:
        raise
    except Exception as exc:
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
) -> str:
    return _openai_compatible_request(
        prompt, api_key, model, max_tokens, base_url, probe=probe
    )


def _complete_openai_compatible_detail(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    base_url: str | None = None,
    *,
    probe: bool = False,
) -> tuple[str | None, str | None]:
    """Call an OpenAI-compatible chat API; return (text, error_message)."""
    try:
        text = _complete_openai_compatible_raw(
            prompt, api_key, model, max_tokens, base_url, probe=probe
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


def _call_llm(prompt: str) -> str | None:
    """Route to the configured provider; return None on any failure."""
    settings = get_settings()
    provider = settings.llm_provider
    api_key = settings.get_active_api_key()
    if not api_key:
        return None
    model = settings.llm_model
    max_tokens = settings.llm_max_tokens

    if provider == "anthropic":
        return _complete_anthropic(prompt, api_key, model, max_tokens)
    if provider == "gemini":
        return _complete_gemini(prompt, api_key, model)

    # All other providers are OpenAI-compatible.
    base_url = _resolve_openai_base_url(provider, settings.llm_base_url)
    return _complete_openai_compatible(prompt, api_key, model, max_tokens, base_url)


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
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


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
    provider = settings.llm_provider
    api_key = settings.get_active_api_key()
    if not api_key:
        return None, "No LLM API key configured"

    model = settings.llm_model
    max_tokens = settings.llm_max_tokens
    schema = model_type.model_json_schema()
    base_url = _resolve_openai_base_url(provider, settings.llm_base_url)

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
        "1. Every chemical component MUST include accurate cas_no and mf (molecular formula).\n"
        "2. Include smiles when known; molar_mass when calculable.\n"
        "3. For coating formulations weight_pct values should sum to approximately 100.\n"
        "4. Populate component_type (resin/hardener/inhibitor/solvent/etc), amount_display, and notes in Chinese where helpful.\n"
        "5. objectives_summary explains how the recipe meets each objective.\n"
        "6. Return JSON only — no markdown fences."
    )


def _constraints_prompt_block(req: Requirement) -> str:
    from ..domain.project_spec import normalize_constraints

    merged = normalize_constraints(req)
    if not merged:
        return "- (none specified)"
    return "\n".join(f"- {k}: {v}" for k, v in merged.items())


def _recommend_user_prompt(
    req: Requirement,
    objectives: list[ObjectiveSpec],
    evidence: list[Evidence],
    n: int,
    *,
    modify_prompt: str = "",
    base_formulas: list | None = None,
) -> str:
    citations = "\n".join(
        f"[{e.source}] {e.title}: {e.snippet[:200]}" for e in evidence[:6]
    ) or "(no external evidence — use domain knowledge)"
    modify_block = ""
    if modify_prompt:
        modify_block = f"\n\n用户修改要求:\n{modify_prompt.strip()}"
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
        f"{_levers_prompt_block(req)}"
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

    system = _recommend_system_prompt()
    user = _recommend_user_prompt(
        req, objectives, evidence, n,
        modify_prompt=modify_prompt,
        base_formulas=base_formulas,
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


def _chat_prompt(question: str, evidence: list[Evidence], domain: str | None) -> str:
    context = "\n".join(
        f"[{i+1}] ({e.source}) {e.title}: {e.snippet[:400]}" for i, e in enumerate(evidence[:8])
    )
    domain_hint = f"Domain context: {domain}\n" if domain else ""
    return (
        f"You are a formulation chemist. Answer the question using ONLY the provided sources. "
        f"Cite sources by number [1], [2], etc.\n"
        f"{domain_hint}\n"
        f"Sources:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"Answer concisely in the same language as the question:"
    )


# ── Offline fallback ─────────────────────────────────────────────────────────

def _offline_synthesis(req: Requirement, evidence: list[Evidence], recommended: list) -> tuple[str, str]:
    """Deterministic rule-based synthesis — works without any API key."""
    domain_names = {
        ProductDomain.anticorrosion_coating: "防腐蚀涂料",
        ProductDomain.degreaser: "脱脂剂",
        ProductDomain.surface_treatment: "表面处理剂",
    }
    d = domain_names.get(req.domain, req.domain.value)
    top = recommended[0] if recommended else None
    mech = (
        f"{d}的核心机理：{'环氧树脂与固化剂形成交联网络，缓蚀剂（磷酸锌等）在界面形成致密保护膜，阻断腐蚀电化学反应。' if req.domain == ProductDomain.anticorrosion_coating else '表面活性剂降低油-水界面张力，使油污乳化脱落；碱性助剂（磷酸钠、碳酸钠）皂化动植物油脂。' if req.domain == ProductDomain.degreaser else '磷化/铬化/硅烷偶联形成转化膜，提升基材与后续涂层的附着力与耐蚀性。'}"
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

_CHEM_KEYWORDS = (
    "smiles", "logp", "溶解度", "solubility", "毒性", "toxicity", "相容", "compatib",
    "molecular weight", "分子量", "反应", "reaction", "synthes", "合成", "structure",
    "结构", "官能团", "functional group", "boiling", "沸点", "melting", "熔点", "pka",
)


def _is_chemistry_question(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in _CHEM_KEYWORDS)


def _chemcrow_available() -> bool:
    try:
        import chemcrow  # noqa: F401

        return True
    except Exception:
        return False


def _chemcrow_llm_ready() -> bool:
    """ChemCrow agent expects an OpenAI-compatible API key."""
    settings = get_settings()
    if settings.openai_api_key:
        return True
    return settings.llm_provider == "openai" and bool(settings.get_active_api_key())


def _paperqa_available() -> bool:
    try:
        import paperqa  # noqa: F401

        return True
    except Exception:
        return False


def _chemcrow_answer(question: str) -> str | None:
    """Route a chemistry question through the ChemCrow ReAct agent."""
    try:  # pragma: no cover - requires chemcrow + API key
        from chemcrow.agents import ChemCrow

        settings = get_settings()
        key = settings.openai_api_key or settings.get_active_api_key()
        if not key:
            return None
        agent = ChemCrow(model=settings.llm_model, temp=0.1, openai_api_key=key)
        result = agent.run(question)
        return str(result) if result else None
    except Exception:
        return None


def _paperqa_answer(
    question: str, sources: list[Evidence]
) -> tuple[str, list[Evidence]] | None:
    """Answer via paper-qa's semantic retrieval + cited synthesis."""
    try:  # pragma: no cover - requires paper-qa + embeddings/LLM
        from paperqa import Docs

        docs = Docs()
        by_key: dict[str, Evidence] = {}
        for ev in sources:
            text = f"{ev.title}. {ev.snippet}".strip()
            if not text:
                continue
            key = ev.identifier or ev.title
            docs.add_texts_from_str(text, citation=ev.title, docname=key) if hasattr(
                docs, "add_texts_from_str"
            ) else docs.add_texts(text, citation=ev.title, docname=key)  # type: ignore
            by_key[key] = ev
        answer = docs.query(question)
        text = getattr(answer, "answer", None) or str(answer)
        cited = [by_key[k] for k in by_key if k in (getattr(answer, "context", "") or "")]
        return text, (cited or sources[:6])
    except Exception:
        return None


def answer_question(
    question: str,
    sources: list[Evidence],
    domain: str | None = None,
) -> tuple[str, list[Evidence]]:
    """Answer a user question grounded in the provided sources.

    Routing (each tier degrades gracefully to the next):
      1. ChemCrow — for chemistry-flavoured questions, when installed + keyed.
      2. paper-qa — semantic retrieval + cited synthesis, when installed.
      3. TF-IDF re-rank → configured multi-LLM provider.
      4. Offline: the most relevant retrieved snippet.

    Returns (answer_text, cited_sources).
    """
    from ..services.rag import build_store

    settings = get_settings()

    # Tier 1: ChemCrow for chemistry questions (requires OpenAI-compatible key).
    if (
        settings.use_chemcrow
        and _chemcrow_llm_ready()
        and _is_chemistry_question(question)
        and _chemcrow_available()
    ):
        cc = _chemcrow_answer(question)
        if cc:
            # Re-rank for citation chips even though ChemCrow answered.
            store = build_store()
            store.ingest(sources)
            relevant = store.query(question, k=min(6, len(sources))) or sources[:6]
            return cc, relevant

    # Re-rank sources by relevance to the question.
    store = build_store()
    store.ingest(sources)
    relevant = store.query(question, k=min(6, len(sources))) or sources[:6]

    # Tier 2: paper-qa semantic synthesis with citations.
    if _paperqa_available() and sources:
        pq = _paperqa_answer(question, sources)
        if pq:
            return pq

    # Tier 3: configured multi-LLM provider over re-ranked sources.
    prompt = _chat_prompt(question, relevant, domain)
    answer = _call_llm(prompt)
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
    provider = settings.llm_provider
    api_key = settings.get_active_api_key()
    model = settings.llm_model
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
        base_url = _resolve_openai_base_url(provider, settings.llm_base_url)
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
