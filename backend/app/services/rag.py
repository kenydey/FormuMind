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
        f"研究主题：{query}"
    )
    try:
        hint = _llm._call_llm(prompt)
    except Exception:
        hint = None
    return f"{query}\n\n{hint}" if hint else query


def _rerank_prompt(query: str, candidates: list[Evidence]) -> str:
    lines = "\n".join(
        f"[{i}] ({e.source}) {e.title}: {e.snippet[:200]}"
        for i, e in enumerate(candidates)
    )
    return (
        "你是检索相关性评审。给定研究主题与若干候选证据，为每条证据按其与主题的"
        "语义相关性打分（0.0 完全无关 … 1.0 高度相关）。\n"
        f"研究主题：{query}\n\n候选证据：\n{lines}\n\n"
        '仅返回 JSON：{"scores": [{"i": 0, "score": 0.9}, ...]}（i 为方括号内编号）。'
    )


def llm_rerank(query: str, candidates: list[Evidence], k: int = 6) -> list[Evidence]:
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
        data = _llm.complete_json(_rerank_prompt(query, candidates))
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
