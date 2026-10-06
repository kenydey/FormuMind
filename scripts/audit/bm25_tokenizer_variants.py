"""Which tokenization gives the application's BM25 channel the best retrieval - and how sure is the answer?

Scores the BM25 channel exactly as ``hybrid_search_scored`` does without a vector channel (``rank_bm25.BM25Okapi`` over the chunk
tokens; one chunk per document in the evaluation corpus), once per tokenizer variant, over the answerable queries of
``backend/evals``. Each variant is compared with the *old* tokenizer (jieba words only) by a paired bootstrap over the queries, so a
difference is shown with the interval it deserves. Evidence behind ``hybrid_search._tokenize`` indexing character pairs
(``backend/evals/README.md``, plan #83); run it again before changing the tokenizer.

    cd backend && python ../scripts/audit/bm25_tokenizer_variants.py [--cost]

``--cost`` also times tokenizing 2,000 chunks of ~700 characters (what one uncached query used to pay) for each variant.
Offline; needs the ``jieba`` and ``rank_bm25`` the application already depends on.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402
from app.services import hybrid_search as hs  # noqa: E402
from evals import metrics as M  # noqa: E402
from evals.datasets import load_corpus, load_queries  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

DOCS = load_corpus()
QUERIES = [q for q in load_queries() if q.category != "unanswerable"]
CATEGORIES = ("lexical", "paraphrase", "numeric", "hard_negative", "crosslingual", "multi_doc")


def _runs(text: str) -> list[str]:
    return hs._CJK_RUN.findall(text.lower())


def _pairs(text: str) -> list[str]:
    return [run[i : i + 2] for run in _runs(text) for i in range(len(run) - 1)]


def jieba_words(text: str) -> list[str]:
    """What the application did before: the dictionary's cut, nothing else."""
    return _cut(text, "cut")


def jieba_search_mode(text: str) -> list[str]:
    return _cut(text, "cut_for_search")


def _cut(text: str, how: str) -> list[str]:
    """Text without Chinese is split by ``_WORD`` alone (as ``hybrid_search._tokenize`` does); jieba only sees Chinese."""
    import jieba

    text = (text or "").strip().lower()
    if not hs._CJK_CHAR.search(text):
        return hs._WORD.findall(text)
    out: list[str] = []
    for piece in getattr(jieba, how)(text):
        out.extend([piece] if hs._CJK_CHAR.search(piece) else hs._WORD.findall(piece))
    return out


def words_and_pairs(text: str) -> list[str]:
    """The application now (``hybrid_search._tokenize``)."""
    return hs._tokenize(text)


def words_pairs_and_characters(text: str) -> list[str]:
    return hs._tokenize(text) + [c for run in _runs(text) if len(run) > 1 for c in run]


def characters_and_pairs(text: str) -> list[str]:
    """No dictionary: ASCII words, then every Chinese character and every overlapping pair."""
    low = (text or "").lower()
    return hs._WORD.findall(low) + [c for run in _runs(low) for c in run] + _pairs(low)


VARIANTS = {
    "jieba words only (the old tokenizer)": jieba_words,
    "jieba search mode": jieba_search_mode,
    "words + pairs (hybrid_search._tokenize)": words_and_pairs,
    "words + pairs + characters": words_pairs_and_characters,
    "characters + pairs, no dictionary": characters_and_pairs,
}


def per_query(tokenize) -> tuple[np.ndarray, np.ndarray]:
    ids = [d.id for d in DOCS]
    bm25 = BM25Okapi([tokenize(d.text) for d in DOCS])
    rows, cats = [], []
    for q in QUERIES:
        scores = bm25.get_scores(tokenize(q.text))
        order = [ids[i] for i in np.argsort(-scores, kind="stable") if scores[i] > 0]
        rows.append((M.success_at_k(order, q.relevant, 1), M.success_at_k(order, q.relevant, 5), M.ndcg_at_k(order, q.relevant, 10)))
        cats.append(q.category)
    return np.array(rows), np.array(cats)


def main() -> None:
    base, cats = per_query(jieba_words)
    resamples = np.random.default_rng(0).integers(0, len(base), size=(4000, len(base)))
    print(f"{len(QUERIES)} answerable queries, {len(DOCS)} documents; BM25 channel only (no vector channel)\n")
    print(f"{'variant':42s} {'s@1':>5} {'s@5':>5} {'nDCG':>5}  {'d nDCG vs old [95% interval]':>30}   " + " ".join(f"{c[:6]:>9}" for c in CATEGORIES))
    for name, tokenize in VARIANTS.items():
        rows, _ = per_query(tokenize)
        diff = rows[:, 2] - base[:, 2]
        low, high = np.percentile(diff[resamples].mean(axis=1), [2.5, 97.5])
        by_cat = " ".join(f"{rows[cats == c, 0].mean():4.2f}/{rows[cats == c, 1].mean():4.2f}" for c in CATEGORIES)
        print(f"{name:42s} {rows[:, 0].mean():5.3f} {rows[:, 1].mean():5.3f} {rows[:, 2].mean():5.3f}  {diff.mean():+8.3f} [{low:+.3f}, {high:+.3f}]    {by_cat}")
    print("\n(per-category cells are success@1/success@5)")

    if "--cost" in sys.argv:
        text = " ".join(d.text for d in DOCS)
        rng = random.Random(1)
        chunks = [text[rng.randrange(0, max(1, len(text) - 700)) :][:700] for _ in range(2000)]
        print("\ntokenizing 2,000 chunks of ~700 characters (hybrid_search._tokenize is cached by text; the others are not):")
        for name, tokenize in VARIANTS.items():
            hs._tokenize_cjk.cache_clear()
            start = time.perf_counter()
            tokens = [tokenize(c) for c in chunks]
            print(f"  {name:42s} {time.perf_counter() - start:5.2f} s   {sum(map(len, tokens)) / len(tokens):5.0f} tokens per chunk")


if __name__ == "__main__":
    main()
