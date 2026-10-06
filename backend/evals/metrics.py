"""Metrics for the evaluation suites: ranking, abstention, parsing, optimisation. Pure functions, no I/O, no app imports.

Everything here is deterministic. Where a number is an average over few cases the suite also reports a seeded bootstrap
interval (``bootstrap_ci``), because "0.71 vs 0.74" over 26 queries is not a difference worth reading.
"""
from __future__ import annotations

import bisect
import math
import random
from collections.abc import Iterable, Sequence

# ── ranking ─────────────────────────────────────────────────────────────────────────────────────


def dedupe_keep_first(ranked: Sequence[str]) -> list[str]:
    """A ranking of chunks becomes a ranking of documents: each document at its best (first) position."""
    seen: set[str] = set()
    out: list[str] = []
    for item in ranked:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def success_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    """1.0 when any relevant document is in the top ``k`` (hit rate / "success@k")."""
    return 1.0 if any(doc in relevant for doc in ranked[:k]) else 0.0


def recall_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int) -> float:
    """Share of the relevant documents found in the top ``k``. Undefined (0.0) without relevant documents."""
    if not relevant:
        return 0.0
    found = sum(1 for doc in set(ranked[:k]) if doc in relevant)
    return found / len(relevant)


def reciprocal_rank(ranked: Sequence[str], relevant: dict[str, int], k: int = 10) -> float:
    for position, doc in enumerate(ranked[:k], start=1):
        if doc in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: dict[str, int], k: int = 10) -> float:
    """Graded nDCG with the usual ``2**grade - 1`` gain and log2 discount."""
    if not relevant:
        return 0.0

    def dcg(grades: Iterable[int]) -> float:
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))

    gained = dcg(relevant.get(doc, 0) for doc in ranked[:k])
    ideal = dcg(sorted(relevant.values(), reverse=True)[:k])
    return gained / ideal if ideal else 0.0


def auroc(scores_pos: Sequence[float], scores_neg: Sequence[float]) -> float | None:
    """Probability that a random positive outscores a random negative (ties count half). None without both classes."""
    if not scores_pos or not scores_neg:
        return None
    wins = 0.0
    for p in scores_pos:
        for n in scores_neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(scores_pos) * len(scores_neg))


# ── aggregation ─────────────────────────────────────────────────────────────────────────────────


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def bootstrap_ci(values: Sequence[float], *, n: int = 1000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Seeded percentile bootstrap interval of the mean. Degenerate (the value twice) for fewer than two cases."""
    if not values:
        return (float("nan"), float("nan"))
    if len(values) < 2:
        return (values[0], values[0])
    rng = random.Random(seed)
    size = len(values)
    means = sorted(sum(values[rng.randrange(size)] for _ in range(size)) / size for _ in range(n))
    lo = means[int((alpha / 2) * n)]
    hi = means[min(n - 1, int((1 - alpha / 2) * n))]
    return (lo, hi)


def summarise(values: Sequence[float], *, seed: int = 0) -> dict[str, float | int]:
    lo, hi = bootstrap_ci(values, seed=seed)
    return {"mean": round(mean(values), 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4), "n": len(values)}


# ── parsing ─────────────────────────────────────────────────────────────────────────────────────

_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-"})
_SPACES = str.maketrans({" ": " ", " ": " ", " ": " ", "　": " "})


def normalise_cell(text: str) -> str:
    """Compare cells the way a reader would: case, spacing and dash variants are not differences; digits and units are."""
    t = (text or "").translate(_DASHES).translate(_SPACES).replace("μ", "µ")
    t = " ".join(t.split())
    return t.casefold()


def longest_increasing(values: Sequence[int]) -> int:
    """Length of the longest strictly increasing subsequence (patience sorting)."""
    tails: list[int] = []
    for v in values:
        at = bisect.bisect_left(tails, v)
        if at == len(tails):
            tails.append(v)
        else:
            tails[at] = v
    return len(tails)


def contains_in_order(haystack: str, needles: Sequence[str]) -> float:
    """Share of ``needles`` that occur in ``haystack`` and in the order given (longest increasing subsequence of positions).

    Reading order of a page: a column-aware parser emits the left column's sentences before the right column's, while one
    that reads straight across the page interleaves them and keeps only about half of the sequence in order. A needle
    that is missing counts against the score.
    """
    hay = normalise_cell(haystack)
    positions = [at for at in (hay.find(normalise_cell(n)) for n in needles) if at >= 0]
    return longest_increasing(positions) / len(needles) if needles else 1.0


def f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


# ── optimisation ────────────────────────────────────────────────────────────────────────────────


def simple_regret(best_so_far: Sequence[float], optimum: float) -> list[float]:
    """Gap to the known optimum after each evaluation (non-increasing for a running best)."""
    return [max(0.0, optimum - b) for b in best_so_far]


def running_best(values: Sequence[float], *, maximise: bool = True) -> list[float]:
    best = -math.inf if maximise else math.inf
    out: list[float] = []
    for v in values:
        best = max(best, v) if maximise else min(best, v)
        out.append(best)
    return out


def win_rate(ours: Sequence[float], theirs: Sequence[float]) -> float:
    """Share of paired runs where ``ours`` ended with the smaller regret (ties count half)."""
    pairs = list(zip(ours, theirs, strict=True))
    if not pairs:
        return float("nan")
    return sum(1.0 if a < b else 0.5 if a == b else 0.0 for a, b in pairs) / len(pairs)
