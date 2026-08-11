"""Retrieval-Augmented knowledge store (OpenNotebook-style interface).

Exposes ``ingest`` / ``query`` mirroring OpenNotebook's document pipeline. When
OpenNotebook is installed it can be delegated to; the built-in fallback is a
self-contained in-memory TF-IDF index (pure Python) that ranks ingested
snippets against a query — enough to ground recommendations with citations
offline.

**Persistence model:** ``TfidfStore`` / ``EmbeddingStore`` from ``build_store()``
are ephemeral per-call re-rankers (ingest sources for one question, then query).
Cross-request knowledge persistence lives in ``colbert_store`` when ColBERT or
the on-disk manifest is enabled — not in the TF-IDF instance.
"""
from __future__ import annotations

import logging
from .errors import log_handled_exception
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..domain.schemas import Evidence, Requirement

logger = logging.getLogger(__name__)

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

# Default only. `embed_model_name()` is the accessor everything should use —
# reading this constant directly ignores the operator's setting.
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def embed_model_name() -> str:
    """The sentence-transformer to embed with.

    Configurable because the default is English-first, and this platform
    retrieves Chinese patents — `all-MiniLM-L6-v2` is a poor fit for the
    corpus it is actually pointed at. Chinese-capable alternatives worth
    setting via ``FORMUMIND_EMBEDDING_MODEL``:

        BAAI/bge-small-zh-v1.5      small, Chinese-first
        BAAI/bge-m3                 multilingual, much larger
        moka-ai/m3e-base            Chinese, mid-sized

    The default is deliberately unchanged. Switching models invalidates every
    stored vector — they live in a different semantic space and are no longer
    comparable — so it is an operator decision, made once, followed by a
    rebuild. `kb_stats` reports ``vector_mode == "stale"`` with the count and
    the instruction when that happens, rather than letting retrieval quietly
    fall back to keywords.
    """
    from ..config import get_settings

    return (get_settings().embedding_model or "").strip() or _EMBED_MODEL


def _embedding_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
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
    # Resolved per instance rather than pinned at class-definition time, so a
    # store built after the setting changes uses the configured model.
    model_name: str = field(default_factory=embed_model_name)
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


# ── BM25 + FAISS hybrid retriever (Phase 2 CPU backend) ───────────────────

def _bm25_tokenize(text: str) -> list[str]:
    """Tokenize for BM25: jieba for CJK, whitespace for ASCII."""
    tokens = _tokenize(text)
    if tokens:
        return tokens
    # Fall back to jieba for Chinese/CJK text
    try:
        import jieba
        return [w for w in jieba.cut(text) if w.strip()]
    except ImportError:
        return text.split()


def _doc_text(ev: Evidence) -> str:
    return f"{ev.title} {ev.snippet}"


@dataclass
class BM25FAISSStore:
    """BM25 + FAISS hybrid retriever — zero AVX2, any x86_64 CPU.

    Uses ``rank_bm25`` for sparse lexical matching + FAISS flat IP index
    for dense semantic matching. Falls back to pure BM25 when FAISS or
    an embedding model is unavailable.

    Hybrid score: ``BM25(0.6) + FAISS_cosine(0.4)``.
    """

    backend: str = "bm25_faiss"
    docs: list[Evidence] = field(default_factory=list)
    bm25_weight: float = 0.6

    def __post_init__(self) -> None:
        self._corpus: list[list[str]] = []
        self._bm25: object | None = None
        self._faiss_index: object | None = None
        self._faiss_dim: int = 0
        self._embedder: object | None = None

    # ── ingest ──────────────────────────────────────────────────────────

    def ingest(self, evidence: list[Evidence]) -> int:
        from rank_bm25 import BM25Okapi

        self.docs.extend(evidence)
        new_corpus = [_bm25_tokenize(_doc_text(ev)) for ev in evidence]
        self._corpus.extend(new_corpus)
        # BM25Okapi divides by the corpus size, so an empty corpus raises
        # ZeroDivisionError rather than building an empty index. Asking a
        # question with no sources attached is an ordinary request, not an
        # error, and it was returning HTTP 500. `query` already treats an
        # empty store as "no hits"; this keeps ingest consistent with it.
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

        # FAISS dense index — only for small batches (<200 docs)
        # to avoid OOM on CPU VPS with sentence-transformers
        if len(evidence) <= 200:
            try:
                self._build_faiss_index(evidence)
            except Exception:
                pass  # FAISS is optional; BM25 alone still works

        return len(self.docs)

    # ── query ───────────────────────────────────────────────────────────

    def query(self, text: str, k: int = 5) -> list[Evidence]:
        if not self.docs:
            return []

        tokens = _bm25_tokenize(text)
        n = min(k, len(self.docs))

        # BM25 scores (sparse)
        if self._bm25 is not None:
            bm25_raw = self._bm25.get_scores(tokens)
            bm25_scores = _minmax_norm(bm25_raw)
        else:
            bm25_scores = [0.5] * len(self.docs)

        # FAISS scores (dense, best-effort)
        faiss_scores = _faiss_scores(self._faiss_index, self._faiss_dim,
                                     self._embedder, text, len(self.docs))

        # Hybrid scoring
        w = self.bm25_weight
        hybrid = [w * b + (1 - w) * f for b, f in zip(bm25_scores, faiss_scores)]

        # Top-k by hybrid score
        order = sorted(range(len(hybrid)), key=lambda i: hybrid[i], reverse=True)[:n]
        return [self.docs[i] for i in order]

    # ── FAISS index builder (internal) ───────────────────────────────────

    def _build_faiss_index(self, evidence: list[Evidence]) -> None:
        import faiss
        import numpy as np

        emb = self._get_embedder()
        texts = [_doc_text(ev) for ev in evidence]
        vecs = np.asarray(emb.encode(texts, normalize_embeddings=True), dtype=np.float32)
        dim = int(vecs.shape[1])
        if self._faiss_index is None:
            self._faiss_index = faiss.IndexFlatIP(dim)
            self._faiss_dim = dim
        self._faiss_index.add(vecs)

    def _get_embedder(self) -> object:
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                # Last resort: random projection (still better than no FAISS)
                import numpy as np
                class _RandomProj:
                    def __init__(self, dim=384):
                        self._proj = np.random.randn(dim, 384).astype(np.float32)
                    def encode(self, texts, normalize_embeddings=False):
                        import numpy as np
                        v = np.random.randn(len(texts), 384).astype(np.float32)
                        if normalize_embeddings:
                            v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-8)
                        return v
                self._embedder = _RandomProj()
        return self._embedder


# ── helpers ─────────────────────────────────────────────────────────────

def _minmax_norm(scores) -> list[float]:
    mn, mx = min(scores), max(scores)
    if mx == mn:
        return [0.5] * len(scores)
    return [(s - mn) / (mx - mn) for s in scores]


def _faiss_scores(index, dim, embedder, text: str, n_docs: int) -> list[float]:
    if index is None or dim == 0:
        return [0.0] * n_docs
    try:
        import numpy as np
        q = np.asarray(embedder.encode([text], normalize_embeddings=True), dtype=np.float32)
        D, I = index.search(q, min(n_docs, index.ntotal))
        scores = [0.0] * n_docs
        for d, i in zip(D[0], I[0]):
            if 0 <= i < n_docs:
                scores[i] = float(max(0.0, d))
        return scores
    except Exception:
        return [0.0] * n_docs


# ── Backend selection ───────────────────────────────────────────────────

def active_rag_backend() -> str:
    """Name of the retrieval backend that ``build_store`` will select."""
    from ..config import get_settings
    from . import colbert_store

    settings = get_settings()
    # Manual override always wins
    if settings.rag_backend not in ("auto", ""):
        return settings.rag_backend
    # GPU mode: try PyLate or legacy ColBERT (only when gpu_enabled)
    if settings.gpu_enabled:
        if colbert_store.colbert_available_gpu(settings):
            return "pylate"
        if colbert_store.colbert_available():
            return "colbert"
    # CPU mode: BM25+FAISS (reliable, no AVX2 requirement)
    return "bm25_faiss"


def build_store():
    """Return the retrieval store used to re-rank evidence for grounded Q&A.

    Priority: BM25+FAISS (CPU default) > EmbeddingStore > TfidfStore.
    ColBERT / PyLate is handled in ``colbert_store.search()`` directly.
    """
    backend = active_rag_backend()
    if backend == "bm25_faiss":
        try:
            return BM25FAISSStore()
        except Exception as exc:
            log_handled_exception(logger, exc, "BM25FAISS init failed, falling back to TF-IDF")
            return TfidfStore()
    if backend == "embedding":
        try:
            return EmbeddingStore()
        except Exception as exc:
            log_handled_exception(logger, exc, "handled exception")
    return TfidfStore()


# ── Advanced RAG: HyDE query expansion + LLM semantic re-ranking ──────────────
# Both are pure enhancements layered on top of build_store(); each degrades to a
# behaviour-preserving no-op when no LLM is configured, so offline retrieval is
# unchanged. They exist to cut "chemical hallucination": HyDE anchors retrieval
# to the *content* an answer would contain (surfacing real citable evidence
# instead of letting the model free-associate), and the re-ranker drops
# off-topic context before synthesis so the LLM is grounded only on the most
# relevant prior art.


def hyde_expand(query: str, domain: str | None = None) -> str:
    """HyDE (Hypothetical Document Embeddings) query expansion.

    Ask the LLM for a short hypothetical technical abstract that an ideal answer
    would resemble, then append it to the query. Embedding/TF-IDF retrieval then
    matches on *meaning* rather than surface keywords, surfacing real evidence to
    ground the answer against. When no LLM is available the original query is
    returned unchanged — retrieval behaviour is identical to before.
    """
    from . import llm as _llm

    ctx = f"（领域：{domain}）" if domain else ""
    prompt = (
        f"针对以下研究主题{ctx}，写一段约 80 词的假设性技术摘要，"
        f"描述理想文献/专利中会出现的关键配方参数、机理与材料。仅输出摘要正文，不要前缀。\n\n"
        f"研究主题：\n<user_query>{query}</user_query>"
    )
    try:
        hint = _llm._call_llm(prompt)
    except Exception:
        hint = None
    return f"{query}\n\n{hint}" if hint else query


def _rerank_query(query: str, req: Requirement | None = None) -> str:
    if req is None:
        return query
    parts = [query.strip()]
    if req.product_type:
        parts.append(f"产品: {req.product_type}")
    if req.substrate:
        parts.append(f"基材: {req.substrate}")
    objs = ", ".join(
        f"{o.metric}({o.direction})" for o in (req.objectives or [])[:4]
    )
    if objs:
        parts.append(f"目标: {objs}")
    mats = ", ".join(m.name for m in (req.materials or [])[:6] if m.name)
    if mats:
        parts.append(f"材料: {mats}")
    return " | ".join(p for p in parts if p)


def _rerank_prompt(query: str, candidates: list[Evidence], req: Requirement | None = None) -> str:
    topic = _rerank_query(query, req)
    lines = "\n".join(
        f"[{i}] ({e.source}) {e.title}: {e.snippet[:200]}"
        for i, e in enumerate(candidates)
    )
    return (
        "你是检索相关性评审。给定研究主题与若干候选证据，为每条证据按其与主题的"
        "语义相关性打分（0.0 完全无关 … 1.0 高度相关）。\n"
        f"研究主题：\n<user_query>{topic}</user_query>\n\n候选证据：\n<candidate_items>{lines}</candidate_items>\n\n"
        '仅返回 JSON：{"scores": [{"i": 0, "score": 0.9}, ...]}（i 为方括号内编号）。'
    )


def llm_rerank(
    query: str,
    candidates: list[Evidence],
    k: int = 6,
    req: Requirement | None = None,
) -> list[Evidence]:
    """Re-rank retrieved candidates by LLM-judged semantic relevance, return top-k.

    Filters off-topic evidence *before* synthesis so the answer engine is
    grounded only on the most relevant prior art. On any failure (no LLM,
    malformed JSON) it returns ``candidates[:k]`` — i.e. the upstream store's
    ordering is preserved, so this is a zero-risk enhancement.
    """
    if not candidates:
        return []
    if len(candidates) <= 1:
        return candidates[:k]

    from . import llm as _llm

    try:
        data = _llm.complete_json(_rerank_prompt(query, candidates, req))
    except Exception:
        data = None

    scores = (data or {}).get("scores") if isinstance(data, dict) else None
    if not isinstance(scores, list):
        return candidates[:k]

    ranking: dict[int, float] = {}
    for item in scores:
        try:
            idx = int(item["i"])
            if 0 <= idx < len(candidates):
                ranking[idx] = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
    if not ranking:
        return candidates[:k]

    # Score-desc; unscored candidates keep original relative order at the tail.
    order = sorted(
        range(len(candidates)),
        key=lambda i: (ranking.get(i, -1.0), -i),
        reverse=True,
    )
    return [candidates[i] for i in order[:k]]
