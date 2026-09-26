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


def _get_client():
    global _client, _client_failed
    if _client_failed:
        return None
    if _client is not None:
        return _client
    try:
        from ..config import get_settings

        settings = get_settings()
        if not getattr(settings, "langfuse_enabled", False):
            return None
        pk = (settings.langfuse_public_key or "").strip()
        sk = (settings.langfuse_secret_key or "").strip()
        if not pk or not sk:
            return None
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=pk,
            secret_key=sk,
            host=(settings.langfuse_host or "https://cloud.langfuse.com").strip(),
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


def shutdown_tracer() -> None:
    global _client
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass
        _client = None
