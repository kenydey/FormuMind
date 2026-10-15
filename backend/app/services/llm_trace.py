"""P1 #26: optional Langfuse LLM tracing (fail-open, default off).

Never raises into call sites. When disabled / missing keys / import failure,
``trace_generation`` is a no-op context manager.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_client = None
_client_failed = False


def reset_client() -> None:
    """Forget the cached Langfuse client (the keys or the host changed in the settings page)."""
    global _client, _client_failed
    _client = None
    _client_failed = False


def _get_client():
    global _client, _client_failed
    if _client_failed:
        return None
    if _client is not None:
        return _client
    try:
        from ..config import get_settings
        from .runtime_secrets import effective_setting, setting_or_default

        settings = get_settings()
        if not settings.langfuse_enabled:
            return None
        pk = str(effective_setting(settings, "langfuse_public_key") or "").strip()
        sk = str(effective_setting(settings, "langfuse_secret_key") or "").strip()
        if not pk or not sk:
            return None
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=pk,
            secret_key=sk,
            host=str(setting_or_default(settings, "langfuse_host")).strip(),
        )
        return _client
    except Exception as exc:
        _client_failed = True
        logger.debug("langfuse unavailable: %s", exc)
        return None


@contextmanager
def trace_generation(
    name: str,
    *,
    input_preview: str = "",
    metadata: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield a mutable bag; callers may set ``output`` / ``error`` before exit."""
    bag: dict[str, Any] = {"output": None, "error": None, "metadata": metadata or {}}
    client = _get_client()
    generation = None
    if client is not None:
        try:
            generation = client.generation(
                name=name,
                input=(input_preview or "")[:2000],
                metadata=bag["metadata"],
            )
        except Exception as exc:
            logger.debug("langfuse generation start failed: %s", exc)
            generation = None
    try:
        yield bag
    finally:
        if generation is not None:
            try:
                if bag.get("error"):
                    generation.end(output=str(bag["error"])[:2000], level="ERROR")
                else:
                    out = bag.get("output")
                    generation.end(
                        output=(str(out)[:2000] if out is not None else None)
                    )
            except Exception as exc:
                logger.debug("langfuse generation end failed: %s", exc)
