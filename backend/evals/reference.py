"""Reference retrievers the production one is read against: a seeded random order (the floor) and a plain BM25 (a
transparent, dependency-free reference with its own tokenizer, so it does not share the production code's blind spots).

A number for the production retriever means little alone; "below the plain BM25 on lexical queries" means a regression.
"""
from __future__ import annotations

import math
import random
import re
from collections import Counter

_CJK_RUN = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """ASCII words and numbers, and character bigrams over each run of Chinese (a single character stands for itself)."""
    lowered = (text or "").lower()
    tokens = _WORD.findall(lowered)
    for run in _CJK_RUN.findall(lowered):
        if len(run) > 1:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


class BM25:
    """Okapi BM25 over a small in-memory corpus."""

    def __init__(self, docs: dict[str, str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.ids = list(docs)
        self.tf = [Counter(tokenize(docs[i])) for i in self.ids]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter[str] = Counter()
        for counts in self.tf:
            df.update(counts.keys())
        n = len(self.ids)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, query: str) -> list[tuple[str, float]]:
        """Every document with its score, best first (ties keep corpus order)."""
        terms = tokenize(query)
        scored: list[tuple[str, float]] = []
        for doc_id, counts, length in zip(self.ids, self.tf, self.len, strict=True):
            s = 0.0
            for t in terms:
                f = counts.get(t, 0)
                if f:
                    s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self.avg))
            scored.append((doc_id, s))
        return sorted(scored, key=lambda x: -x[1])

    def normalised_top_score(self, query: str) -> float:
        """Best score per query term - comparable across queries of different length (for the abstention measure)."""
        terms = tokenize(query)
        ranked = self.score(query)
        return ranked[0][1] / len(terms) if ranked and terms else 0.0


def random_ranking(doc_ids: list[str], *, seed: int, key: str) -> list[str]:
    rng = random.Random(f"{seed}:{key}")
    order = list(doc_ids)
    rng.shuffle(order)
    return order
