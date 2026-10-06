"""Retrieval: does the right document come back for the question, and how does that compare with a plain BM25 and a coin toss?

The production retriever is run exactly as chat reaches it - documents ingested through ``ingest_document_tx``, queried
through ``kb_index.retrieve_evidence(mode="hybrid")`` - against a corpus of 52 short coating-R&D documents (44 Chinese, 8
English, eleven topic clusters whose members deliberately share vocabulary) and 86 queries labelled with graded relevance.
The queries come in categories because one average hides what matters: ``lexical`` (the question repeats the document's
words), ``paraphrase`` (it avoids them), ``numeric`` (figures tell near-identical documents apart), ``hard_negative``
(neighbours share the vocabulary), ``crosslingual``, ``multi_doc`` and ``unanswerable`` (no document answers; scored
separately, as abstention).

Documents are collapsed from chunks to ids (best rank per document). Relevance is at document level.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from .. import metrics as M
from ..datasets import Doc, Query, load_corpus, load_queries
from ..env import isolated_kb
from ..reference import BM25, random_ranking

K = 10
RANK_METRICS = ("success@1", "success@3", "success@5", "recall@5", "recall@10", "mrr@10", "ndcg@10")


@dataclass(frozen=True)
class Hit:
    doc_id: str
    score: float
    snippet: str


Retriever = Callable[[str, int], list[Hit]]


def _rank_metrics(ranked: Sequence[str], relevant: dict[str, int]) -> dict[str, float]:
    return {
        "success@1": M.success_at_k(ranked, relevant, 1),
        "success@3": M.success_at_k(ranked, relevant, 3),
        "success@5": M.success_at_k(ranked, relevant, 5),
        "recall@5": M.recall_at_k(ranked, relevant, 5),
        "recall@10": M.recall_at_k(ranked, relevant, 10),
        "mrr@10": M.reciprocal_rank(ranked, relevant, 10),
        "ndcg@10": M.ndcg_at_k(ranked, relevant, 10),
    }


# ── the systems ──────────────────────────────────────────────────────────────────────────────────


@contextmanager
def production_retriever(docs: Sequence[Doc]) -> Iterator[Retriever]:
    """The application's retriever over a throwaway knowledge base holding ``docs``."""
    with isolated_kb():
        from app.db.database import default_session_factory
        from app.services import kb_index
        from app.services.ingest_tx import ingest_document_tx

        factory = default_session_factory()
        for doc in docs:
            result = ingest_document_tx(factory, source_id=doc.id, text=doc.text, title=doc.title)
            if result.failed or result.chunk_count < 1:
                raise RuntimeError(f"the knowledge base refused document {doc.id!r} ({result})")

        def retrieve(query: str, k: int) -> list[Hit]:
            hits: list[Hit] = []
            for ev in kb_index.retrieve_evidence(query, k=k, mode="hybrid"):
                ident = ev.identifier or ""
                if ident.startswith("kb:") and "#" in ident:
                    hits.append(Hit(ident[3:].split("#", 1)[0], float(ev.relevance or 0.0), ev.snippet or ""))
            return hits

        yield retrieve


def _bm25_retriever(docs: Sequence[Doc]) -> tuple[Retriever, BM25]:
    bm25 = BM25({d.id: d.text for d in docs})
    text = {d.id: d.text for d in docs}

    def retrieve(query: str, k: int) -> list[Hit]:
        return [Hit(i, s, text[i]) for i, s in bm25.score(query)[:k] if s > 0]

    return retrieve, bm25


def _random_retriever(docs: Sequence[Doc], seed: int) -> Retriever:
    ids = [d.id for d in docs]
    text = {d.id: d.text for d in docs}

    def retrieve(query: str, k: int) -> list[Hit]:
        return [Hit(i, 0.0, text[i]) for i in random_ranking(ids, seed=seed, key=query)[:k]]

    return retrieve


# ── running ──────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Collected:
    """What each system returned for each query; shared with the QA suite so the corpus is ingested once."""

    docs: list[Doc]
    queries: list[Query]
    hits: dict[str, dict[str, list[Hit]]]  # system -> query id -> hits (best first)
    config: dict
    bm25_top_scores: dict[str, float]  # query id -> the reference BM25's best score per query term


def collect(
    *,
    k: int = K,
    seed: int = 0,
    docs: Sequence[Doc] | None = None,
    queries: Sequence[Query] | None = None,
    production: Retriever | None = None,
    use_production: bool = True,
) -> Collected:
    docs = list(docs if docs is not None else load_corpus())
    queries = list(queries if queries is not None else load_queries())
    bm25_retrieve, bm25 = _bm25_retriever(docs)
    systems: dict[str, Retriever] = {"bm25_reference": bm25_retrieve, "random": _random_retriever(docs, seed)}
    config: dict = {"k": k, "seed": seed, "corpus_documents": len(docs), "queries": len(queries)}

    def run_all(retriever: Retriever) -> dict[str, list[Hit]]:
        return {q.id: retriever(q.text, k) for q in queries}

    hits: dict[str, dict[str, list[Hit]]] = {name: run_all(fn) for name, fn in systems.items()}
    if production is not None:
        hits["production"] = run_all(production)
        config["production"] = "injected retriever"
    elif use_production:
        with production_retriever(docs) as retrieve:
            hits["production"] = run_all(retrieve)
            from app.services import hybrid_search, kb_index

            config["production"] = "kb_index.retrieve_evidence(mode=hybrid) over the ingested corpus"
            config["vector_channel"] = bool(kb_index._embedding_probe())
            config["bm25_tokenizer"] = "jieba" if _has_jieba() else "characters"
            config["hybrid_alpha"] = float(hybrid_search.get_settings().kb_hybrid_alpha)
    return Collected(docs, queries, hits, config, {q.id: bm25.normalised_top_score(q.text) for q in queries})


def _has_jieba() -> bool:
    try:
        import jieba  # noqa: F401
    except ImportError:
        return False
    return True


def _first_relevant_rank(ranked: Sequence[str], relevant: dict[str, int]) -> int | None:
    for position, doc in enumerate(ranked, start=1):
        if doc in relevant:
            return position
    return None


def evaluate(collected: Collected) -> dict:
    answerable = [q for q in collected.queries if q.answerable]
    unanswerable = [q for q in collected.queries if not q.answerable]
    systems: dict[str, dict] = {}
    per_query: list[dict] = []
    for system, by_query in collected.hits.items():
        rows: dict[str, list[dict[str, float]]] = defaultdict(list)
        overall: dict[str, list[float]] = defaultdict(list)
        for q in answerable:
            ranked = M.dedupe_keep_first([h.doc_id for h in by_query[q.id]])
            m = _rank_metrics(ranked, q.relevant)
            rows[q.category].append(m)
            for name, value in m.items():
                overall[name].append(value)
        systems[system] = {
            "overall": {name: M.summarise(values) for name, values in overall.items()},
            "by_category": {
                cat: {name: round(M.mean([r[name] for r in rs]), 4) for name in RANK_METRICS} | {"n": len(rs)}
                for cat, rs in sorted(rows.items())
            },
        }
    for q in collected.queries:
        per_query.append(
            {
                "id": q.id,
                "category": q.category,
                **{
                    f"{system}_first_relevant_rank": _first_relevant_rank(
                        M.dedupe_keep_first([h.doc_id for h in by_query[q.id]]), q.relevant
                    )
                    for system, by_query in collected.hits.items()
                    if q.answerable
                },
            }
        )

    abstention: dict[str, dict] = {}
    for system, by_query in collected.hits.items():
        def top(q: Query, by_query=by_query, system=system) -> float:
            if system == "bm25_reference":
                return collected.bm25_top_scores[q.id]
            hits = by_query[q.id]
            return hits[0].score if hits else 0.0

        pos = [top(q) for q in answerable]
        neg = [top(q) for q in unanswerable]
        abstention[system] = {
            "auroc": None if M.auroc(pos, neg) is None else round(M.auroc(pos, neg), 4),
            "top1_answerable_mean": round(M.mean(pos), 4),
            "top1_unanswerable_mean": round(M.mean(neg), 4),
            "n_answerable": len(pos),
            "n_unanswerable": len(neg),
        }
    return {"suite": "retrieval", "config": collected.config, "systems": systems, "abstention": abstention, "per_query": per_query}


def run(*, k: int = K, seed: int = 0, collected: Collected | None = None, **kwargs) -> dict:
    return evaluate(collected or collect(k=k, seed=seed, **kwargs))
