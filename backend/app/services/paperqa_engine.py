"""PaperQA evidence engine — decoupled from ``llm.py`` (Wave B).

Availability no longer hard-requires ``openai_api_key``. Uses the active chat
LLM (DeepSeek / OpenAI-compat) when configured; embeddings fall back or skip.
Fail-open: any error → ``None`` so callers use hybrid RAG.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

from ..domain.schemas import Evidence
from ..services.errors import degrade_return, optional_import
from .query_aware_compression import (
    compress_evidence,
    llm_compress_evidence,
    query_compress_enabled,
    query_compress_llm_enabled,
    query_compress_token_budget,
)

logger = logging.getLogger(__name__)


def _settings():
    from ..config import get_settings

    return get_settings()


def paperqa_enabled(settings: Any | None = None) -> bool:
    s = settings or _settings()
    return bool(getattr(s, "paperqa_enabled", True))


def _has_llm_credentials(settings: Any) -> bool:
    """True when chat/active provider has a usable API key."""
    try:
        key = settings.get_active_api_key()
        if key:
            return True
    except Exception:  # noqa: BLE001
        pass
    from ..services.runtime_secrets import effective_setting

    for attr in ("openai_api_key", "deepseek_api_key", "anthropic_api_key"):
        if effective_setting(settings, attr):
            return True
    return False


def paperqa_available(settings: Any | None = None) -> bool:
    """Package installed + flag on + some LLM credential present."""
    if not optional_import("paperqa"):
        return False
    s = settings or _settings()
    if not paperqa_enabled(s):
        return False
    return _has_llm_credentials(s)


def _resolve_llm_bundle(settings: Any) -> dict[str, Any]:
    """Return model/base_url/api_key for PaperQA / litellm."""
    from ..services.runtime_secrets import effective_setting
    from .llm import _resolve_openai_base_url

    provider = str(effective_setting(settings, "llm_provider") or "openai")
    model = str(
        getattr(settings, "paperqa_llm_model", None)
        or effective_setting(settings, "llm_model")
        or "gpt-4o-mini"
    ).strip()
    api_key = settings.get_active_api_key() or effective_setting(settings, "openai_api_key") or ""
    base_url = _resolve_openai_base_url(
        provider, effective_setting(settings, "llm_base_url")
    )
    embedding = str(getattr(settings, "paperqa_embedding", None) or "").strip()
    openai_key = effective_setting(settings, "openai_api_key")
    if not embedding:
        # Prefer OpenAI embeddings only when an OpenAI key exists; else mark empty
        # so callers can skip / use paperqa default carefully.
        embedding = "text-embedding-3-small" if openai_key else ""
    return {
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "embedding": embedding,
        "openai_api_key": openai_key or "",
    }


@contextmanager
def _litellm_env(bundle: dict[str, Any]) -> Iterator[None]:
    """Temporarily expose OpenAI-compat credentials for litellm/paperqa."""
    saved: dict[str, str | None] = {}
    updates: dict[str, str] = {}
    key = bundle.get("api_key") or ""
    base = bundle.get("base_url") or ""
    if key:
        updates["OPENAI_API_KEY"] = str(key)
    if base:
        updates["OPENAI_API_BASE"] = str(base)
        updates["OPENAI_BASE_URL"] = str(base)
    for k, v in updates.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        yield
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _build_paperqa_settings(bundle: dict[str, Any]):
    """Best-effort Settings object; None if paperqa.Settings unavailable."""
    try:
        from paperqa import Settings
    except Exception:  # noqa: BLE001
        return None
    model = bundle["model"]
    # litellm OpenAI-compat path
    llm_name = model if "/" in model else f"openai/{model}"
    kwargs: dict[str, Any] = {
        "llm": llm_name,
        "summary_llm": llm_name,
    }
    if bundle.get("embedding"):
        emb = bundle["embedding"]
        kwargs["embedding"] = emb if "/" in emb else f"openai/{emb}"
    llm_config: dict[str, Any] = {}
    if bundle.get("api_key"):
        llm_config["api_key"] = bundle["api_key"]
    if bundle.get("base_url"):
        llm_config["api_base"] = bundle["base_url"]
    if llm_config:
        kwargs["llm_config"] = llm_config
        kwargs["summary_llm_config"] = dict(llm_config)
    try:
        return Settings(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("paperqa Settings(**kwargs) failed (%s); retry minimal", exc)
        try:
            return Settings(llm=llm_name, summary_llm=llm_name)
        except Exception:  # noqa: BLE001
            return None


async def answer_with_paperqa_async(
    question: str,
    sources: list[Evidence],
    *,
    settings: Any | None = None,
) -> tuple[str, list[Evidence]] | None:
    """Async PaperQA synthesis over provided Evidence snippets."""
    s = settings or _settings()
    if not paperqa_available(s) or not sources:
        return None
    # W5-1 (P2-1): query-aware evidence compression before synthesis.
    # Tier 1 is cheap and fail-open; the kill-switch defaults ON.
    # Tier 2 (LLM rewrite) runs first when enabled, then tier 1 budget-fits.
    if query_compress_enabled(s):
        before = len(sources)
        if query_compress_llm_enabled(s):
            sources = llm_compress_evidence(question, sources, settings=s)
        sources = compress_evidence(
            question, sources, token_budget=query_compress_token_budget(s)
        )
        logger.debug("query compression: %d -> %d evidence", before, len(sources))
    bundle = _resolve_llm_bundle(s)
    try:
        from paperqa import Docs, Doc, Text

        pq_settings = _build_paperqa_settings(bundle)
        docs = Docs()
        by_key: dict[str, Evidence] = {}
        with _litellm_env(bundle):
            for i, ev in enumerate(sources):
                text = f"{ev.title}. {ev.snippet}".strip()
                if not text:
                    continue
                key = ev.identifier or ev.title or str(i)
                doc = Doc(docname=key, citation=ev.source or "", dockey=str(i))
                add_kwargs: dict[str, Any] = {}
                if pq_settings is not None:
                    add_kwargs["settings"] = pq_settings
                try:
                    await docs.aadd_texts([Text(text=text, name=key, doc=doc)], doc, **add_kwargs)
                except TypeError:
                    await docs.aadd_texts([Text(text=text, name=key, doc=doc)], doc)
                by_key[key] = ev
            if not by_key:
                return None
            query_kwargs: dict[str, Any] = {}
            if pq_settings is not None:
                query_kwargs["settings"] = pq_settings
            try:
                answer = await docs.aquery(question, **query_kwargs)
            except TypeError:
                answer = await docs.aquery(question)
        text = getattr(answer, "answer", None) or str(answer)
        cited = [by_key[k] for k in by_key if k in (getattr(answer, "context", "") or "")]
        # P1-a: 引用诚实性 —— 未解析出引用时返回空，不用无关 source 充数
        return text, cited
    except Exception as exc:  # noqa: BLE001
        return degrade_return(logger, exc, "paperqa engine failed", None)


def answer_with_paperqa(
    question: str,
    sources: list[Evidence],
    *,
    settings: Any | None = None,
) -> tuple[str, list[Evidence]] | None:
    """Sync wrapper; returns None if an event loop is already running."""
    if not paperqa_available(settings):
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            answer_with_paperqa_async(question, sources, settings=settings)
        )
    return None


# Back-compat aliases used by thin wrappers in llm.py
_paperqa_available = paperqa_available
_paperqa_answer = answer_with_paperqa_async
_run_paperqa = answer_with_paperqa
