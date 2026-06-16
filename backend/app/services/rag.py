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


def build_store() -> TfidfStore:
    """Return the retrieval store used to re-rank evidence for grounded Q&A.

    The in-memory TF-IDF index is the reliable default and the offline
    fallback. Semantic synthesis (paper-qa) and the chemistry agent (ChemCrow)
    are layered on top in ``llm.answer_question`` rather than here, because they
    are end-to-end answer engines, not drop-in re-rankers. Keeping retrieval and
    synthesis separate lets each tier degrade independently.
    """
    return TfidfStore()
