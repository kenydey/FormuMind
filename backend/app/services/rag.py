"""Retrieval-Augmented knowledge store (OpenNotebook-style interface).

Exposes ``ingest`` / ``query`` mirroring OpenNotebook's document pipeline. When
OpenNotebook is installed it can be delegated to; the built-in fallback is a
self-contained in-memory TF-IDF index (pure Python) that ranks ingested
snippets against a query — enough to ground recommendations with citations
offline.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..domain.schemas import Evidence

_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


@dataclass
class TfidfStore:
    backend: str = "tfidf"
    docs: list[Evidence] = field(default_factory=list)
    _tokens: list[list[str]] = field(default_factory=list)
    _df: Counter = field(default_factory=Counter)

    def ingest(self, evidence: list[Evidence]) -> int:
        for ev in evidence:
            toks = _tokenize(f"{ev.title} {ev.snippet}")
            self.docs.append(ev)
            self._tokens.append(toks)
            for t in set(toks):
                self._df[t] += 1
        return len(self.docs)

    def _idf(self, term: str) -> float:
        n = len(self.docs)
        return math.log((1 + n) / (1 + self._df.get(term, 0))) + 1.0

    def query(self, text: str, k: int = 5) -> list[Evidence]:
        if not self.docs:
            return []
        q_tokens = _tokenize(text)
        q_vec = {t: self._idf(t) for t in set(q_tokens)}
        scored: list[tuple[float, Evidence]] = []
        for toks, ev in zip(self._tokens, self.docs):
            tf = Counter(toks)
            length = len(toks) or 1
            score = sum((tf[t] / length) * self._idf(t) * q_vec[t] for t in q_vec if t in tf)
            scored.append((score, ev))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [ev for score, ev in scored[:k] if score > 0] or self.docs[:k]


# ── Optional semantic embedding store (sentence-transformers) ────────────────
# A drop-in re-ranker that understands meaning ("epoxy" ≈ "bisphenol-A"), unlike
# the lexical TF-IDF index. Gated behind an availability probe; build_store()
# selects it when installed and configured, else falls back to TF-IDF. The model
# (all-MiniLM-L6-v2, ~22 MB) runs CPU-only; embeddings are cosine-compared with
# numpy, so no vector database is needed for the ephemeral per-request store.

_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _embedding_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except Exception:
        return False


# Cache the loaded model so repeated chat requests don't reload it.
_MODEL_CACHE: dict[str, object] = {}


def _load_model(name: str):
    if name not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer

        _MODEL_CACHE[name] = SentenceTransformer(name)
    return _MODEL_CACHE[name]


@dataclass
class EmbeddingStore:
    """Semantic retrieval over sentence-transformer embeddings (cosine sim)."""

    backend: str = "embedding"
    model_name: str = _EMBED_MODEL
    docs: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._model = _load_model(self.model_name)
        self._mat = None  # np.ndarray of normalized doc embeddings

    def ingest(self, evidence: list[Evidence]) -> int:
        import numpy as np

        texts = [f"{ev.title} {ev.snippet}" for ev in evidence]
        self.docs.extend(evidence)
        if texts:
            embs = np.asarray(
                self._model.encode(texts, normalize_embeddings=True), dtype=float
            )
            self._mat = embs if self._mat is None else np.vstack([self._mat, embs])
        return len(self.docs)

    def query(self, text: str, k: int = 5) -> list[Evidence]:
        if not self.docs or self._mat is None:
            return []
        import numpy as np

        q = np.asarray(
            self._model.encode([text], normalize_embeddings=True), dtype=float
        )[0]
        sims = self._mat @ q
        order = np.argsort(sims)[::-1][:k]
        return [self.docs[i] for i in order]


def active_rag_backend() -> str:
    """Name of the retrieval backend that ``build_store`` will select.

    Cheap to call (no model load) so API responses can report it.
    """
    from ..config import get_settings

    settings = get_settings()
    if settings.rag_backend in ("embedding", "auto") and _embedding_available():
        return "embedding"
    return "tfidf"


def build_store():
    """Return the retrieval store used to re-rank evidence for grounded Q&A.

    Priority: the semantic ``EmbeddingStore`` (when sentence-transformers is
    installed and ``rag_backend`` allows it) over the in-memory TF-IDF index,
    which remains the reliable default and offline fallback. Semantic synthesis
    (paper-qa) and the chemistry agent (ChemCrow) are layered on top in
    ``llm.answer_question`` rather than here, because they are end-to-end answer
    engines, not drop-in re-rankers. Keeping retrieval and synthesis separate
    lets each tier degrade independently.
    """
    if active_rag_backend() == "embedding":
        try:
            return EmbeddingStore()
        except Exception:
            pass
    return TfidfStore()
