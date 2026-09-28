"""P1-21: cross-encoder rerank as an optional, fail-open plugin (Wave 3).

Default off (``rerank_plugin_enabled=False``). When enabled, the plugin chain
is: cross-encoder -> LLM rerank -> upstream order. Every stage degrades
gracefully: missing ``sentence_transformers``, model-load failure, or a
scoring crash all preserve the upstream order with a warning — nothing here
ever raises for a missing package or model.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import get_settings

logger = logging.getLogger(__name__)

#: The plugin chain only engages for k at or below this (cost control).
MAX_PLUGIN_K = 50

_DEFAULT_MODEL = "BAAI/bge-reranker-base"

# B-16: cross-encoder weights are GB-scale; cache loaded models by name so
# repeated rerank calls reuse instead of reloading. Bounded to the 2 most
# recently used — more would pin multiple GBs of weights in RAM.
_MODEL_CACHE: OrderedDict[str, Any] = OrderedDict()
_MODEL_CACHE_MAX = 2
_MODEL_CACHE_LOCK = threading.Lock()


def _cached_cross_encoder(model_name: str) -> Any:
    """Return the cached CrossEncoder for ``model_name``, loading once."""
    with _MODEL_CACHE_LOCK:
        hit = _MODEL_CACHE.get(model_name)
        if hit is not None:
            _MODEL_CACHE.move_to_end(model_name)
            return hit
    # Load outside the lock (slow), then insert with eviction under the lock.
    # A rare double-load race only wastes one load; the winner is shared after.
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(model_name)
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[model_name] = model
        _MODEL_CACHE.move_to_end(model_name)
        while len(_MODEL_CACHE) > _MODEL_CACHE_MAX:
            evicted, _ = _MODEL_CACHE.popitem(last=False)
            logger.info("rerank model cache evicted %s", evicted)
    return model


def invalidate_rerank_model_cache() -> None:
    """Drop cached cross-encoder models (tests / memory pressure)."""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


class Reranker(Protocol):
    """Scoring backend for the rerank plugin chain."""

    name: str

    def rerank(
        self, query: str, candidates: list[Any], k: int
    ) -> tuple[list[Any], bool]:
        """Return ``(top_k, applied)``.

        ``applied=False`` means the backend could not score and the caller
        should keep the upstream order.
        """
        ...


@dataclass
class NullReranker:
    """Degraded reranker: preserves upstream order, never fails."""

    name: str = "none"

    def rerank(
        self, query: str, candidates: list[Any], k: int
    ) -> tuple[list[Any], bool]:
        return list(candidates[:k]), False


class CrossEncoderReranker:
    """Cross-encoder reranker with lazy import and graceful degradation.

    ``sentence_transformers`` is imported lazily — never at module import
    time. ImportError degrades to :class:`NullReranker` behaviour immediately;
    model-load or predict failures degrade on first use. Degradation keeps the
    upstream order, logs a warning, and never raises.
    """

    name = "cross_encoder"

    def __init__(self, model_name: str = "") -> None:
        self.model_name = (model_name or "").strip() or _DEFAULT_MODEL
        self._degraded = False
        self._model: Any = None
        try:
            from sentence_transformers import CrossEncoder  # noqa: F401
        except Exception as exc:  # ImportError and friends
            logger.warning(
                "cross-encoder unavailable (%s); rerank plugin degraded", exc
            )
            self._degraded = True

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _ensure_model(self) -> bool:
        if self._degraded:
            return False
        if self._model is None:
            try:
                # B-16: shared process-wide cache — repeated rerank calls
                # (and separate CrossEncoderReranker instances) reuse the
                # loaded weights instead of reloading GBs per call.
                self._model = _cached_cross_encoder(self.model_name)
            except Exception as exc:
                logger.warning(
                    "cross-encoder model load failed (%s); rerank plugin degraded",
                    exc,
                )
                self._degraded = True
                return False
        return True

    @staticmethod
    def _doc_text(candidate: Any) -> str:
        title = getattr(candidate, "title", "") or ""
        snippet = getattr(candidate, "snippet", "") or ""
        text = f"{str(title).strip()} {str(snippet).strip()}".strip()
        return text or str(candidate)

    def rerank(
        self, query: str, candidates: list[Any], k: int
    ) -> tuple[list[Any], bool]:
        if not candidates:
            return [], False
        if not self._ensure_model():
            return list(candidates[:k]), False
        try:
            pairs = [[query, self._doc_text(c)] for c in candidates]
            raw = self._model.predict(pairs)
            scores = [float(s) for s in list(raw)]
        except Exception as exc:
            logger.warning(
                "cross-encoder predict failed (%s); keeping upstream order", exc
            )
            self._degraded = True
            # B-16: 坏模型逐出进程缓存 —— 新实例 retry 时重新加载权重
            # （瞬时 GPU/OOM 错误可恢复），而不是永远复用这个坏对象。
            with _MODEL_CACHE_LOCK:
                _MODEL_CACHE.pop(self.model_name, None)
            return list(candidates[:k]), False
        if len(scores) != len(candidates):
            logger.warning(
                "cross-encoder score length mismatch (%d vs %d); not applied",
                len(scores),
                len(candidates),
            )
            return list(candidates[:k]), False
        order = sorted(
            range(len(candidates)),
            key=lambda i: (scores[i], -i),
            reverse=True,
        )
        return [candidates[i] for i in order[:k]], True


def build_reranker(model_name: str = "") -> Reranker:
    """Build the cross-encoder reranker, degraded to null when unavailable."""
    reranker = CrossEncoderReranker(model_name)
    if reranker.degraded:
        return NullReranker()
    return reranker


def _llm_fallback_scored(
    query: str, candidates: list[Any], k: int, req: Any = None
) -> tuple[list[Any], bool]:
    """Existing LLM rerank (lazy import to avoid a module cycle)."""
    from .rag import llm_rerank_scored

    return llm_rerank_scored(query, candidates, k=k, req=req)


def rerank_candidates(
    query: str, candidates: list[Any], k: int = 6, req: Any = None
) -> tuple[list[Any], str]:
    """Plugin chain: cross-encoder -> llm_rerank -> upstream order.

    Returns ``(items, applied)`` where ``applied`` is one of
    ``"cross_encoder" | "llm" | "none"``. Never raises: any unexpected failure
    keeps the upstream order with ``applied="none"``.
    """
    try:
        settings = get_settings()
        enabled = bool(getattr(settings, "rerank_plugin_enabled", False))
        model_name = str(getattr(settings, "rerank_model", "") or "")
    except Exception as exc:
        logger.warning("rerank plugin settings unreadable (%s); skipping", exc)
        return list(candidates[:k]), "none"

    if not enabled:
        return list(candidates[:k]), "none"
    if k > MAX_PLUGIN_K:
        logger.warning(
            "rerank plugin skipped: k=%d exceeds cap %d", k, MAX_PLUGIN_K
        )
        return list(candidates[:k]), "none"
    if not candidates:
        return [], "none"

    try:
        reranker = build_reranker(model_name)
        items, applied = reranker.rerank(query, candidates, k)
        if applied:
            logger.info("rerank plugin applied via cross_encoder (k=%d)", k)
            return items, "cross_encoder"
    except Exception as exc:  # never let a plugin crash the retrieval path
        logger.warning(
            "cross-encoder rerank crashed (%s); trying llm fallback", exc
        )

    try:
        scored, llm_applied = _llm_fallback_scored(query, candidates, k, req)
        if llm_applied:
            return [s.evidence for s in scored], "llm"
    except Exception as exc:
        logger.warning(
            "llm rerank fallback failed (%s); keeping upstream order", exc
        )
    return list(candidates[:k]), "none"
