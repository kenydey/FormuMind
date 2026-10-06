"""The evaluation harness's metrics (``backend/evals/metrics.py``): pure functions, pinned on cases whose answer is known."""
from __future__ import annotations

import math

import pytest
from evals import metrics as M


def test_success_and_recall_at_k():
    relevant = {"a": 3, "b": 1}
    assert M.success_at_k(["x", "a", "y"], relevant, 1) == 0.0
    assert M.success_at_k(["x", "a", "y"], relevant, 2) == 1.0
    assert M.recall_at_k(["a", "x", "b"], relevant, 1) == 0.5
    assert M.recall_at_k(["a", "x", "b"], relevant, 3) == 1.0
    assert M.recall_at_k(["a", "a", "a"], relevant, 3) == 0.5  # a document found twice is found once
    assert M.recall_at_k(["a"], {}, 5) == 0.0  # nothing to find: undefined, reported as 0


def test_reciprocal_rank():
    relevant = {"a": 1}
    assert M.reciprocal_rank(["x", "y", "a"], relevant) == pytest.approx(1 / 3)
    assert M.reciprocal_rank(["x", "y", "a"], relevant, k=2) == 0.0
    assert M.reciprocal_rank([], relevant) == 0.0


def test_graded_ndcg_rewards_the_better_document_first():
    relevant = {"a": 3, "b": 1}
    assert M.ndcg_at_k(["a", "b"], relevant) == pytest.approx(1.0)
    ideal = 7 / math.log2(2) + 1 / math.log2(3)  # gains 2**grade - 1, discount log2(rank + 1)
    assert M.ndcg_at_k(["b", "a"], relevant) == pytest.approx((1 / math.log2(2) + 7 / math.log2(3)) / ideal)
    assert M.ndcg_at_k(["x", "y"], relevant) == 0.0
    assert M.ndcg_at_k(["a"], {}) == 0.0


def test_a_chunk_ranking_becomes_a_document_ranking_at_each_documents_best_position():
    assert M.dedupe_keep_first(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_auroc():
    assert M.auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert M.auroc([0.1, 0.2], [0.9, 0.8]) == 0.0
    assert M.auroc([0.5, 0.5], [0.5, 0.5]) == 0.5  # no information: ties count half
    assert M.auroc([], [0.1]) is None and M.auroc([0.1], []) is None


def test_bootstrap_interval_is_seeded_and_brackets_the_mean():
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    assert M.bootstrap_ci(values, seed=1) == M.bootstrap_ci(values, seed=1)
    lo, hi = M.bootstrap_ci(values)
    assert lo <= M.mean(values) <= hi and lo < hi
    assert M.bootstrap_ci([0.7]) == (0.7, 0.7)  # one case has no spread to resample
    assert all(math.isnan(v) for v in M.bootstrap_ci([]))
    summary = M.summarise(values)
    assert set(summary) == {"mean", "ci_low", "ci_high", "n"} and summary["n"] == 8


def test_cells_are_compared_the_way_a_reader_would():
    assert M.normalise_cell("  50 µm ") == M.normalise_cell("50 μm")  # no-break space; micro sign vs Greek mu
    assert M.normalise_cell("8.5–9.0") == M.normalise_cell("8.5-9.0") == M.normalise_cell("8.5−9.0")
    assert M.normalise_cell("ASTM  B117") == "astm b117"
    assert M.normalise_cell("50 µm") != M.normalise_cell("5 µm")  # digits are not formatting


def test_longest_increasing_subsequence():
    assert M.longest_increasing([]) == 0
    assert M.longest_increasing([1, 2, 3]) == 3
    assert M.longest_increasing([3, 2, 1]) == 1
    assert M.longest_increasing([0, 2, 4, 6, 1, 3, 5, 7]) == 5
    assert M.longest_increasing([1, 1, 1]) == 1  # strictly


def test_reading_order_score():
    assert M.contains_in_order("a b c d", ["a", "b", "c", "d"]) == 1.0
    assert M.contains_in_order("a x b y", ["a", "b", "c", "d"]) == 0.5  # c and d missing count against it
    assert M.contains_in_order("L1 R1 L2 R2 L3 R3 L4 R4", ["L1", "L2", "L3", "L4", "R1", "R2", "R3", "R4"]) == pytest.approx(0.625)
    assert M.contains_in_order("anything", []) == 1.0


def test_win_rate_counts_ties_half_and_wants_paired_runs():
    assert M.win_rate([0.1, 0.2], [0.5, 0.5]) == 1.0
    assert M.win_rate([0.5, 0.2], [0.5, 0.5]) == 0.75
    assert math.isnan(M.win_rate([], []))
    with pytest.raises(ValueError):
        M.win_rate([0.1], [0.1, 0.2])


def test_running_best_and_regret():
    assert M.running_best([1, 3, 2, 5, 4]) == [1, 3, 3, 5, 5]
    assert M.running_best([5, 3, 4, 1], maximise=False) == [5, 3, 3, 1]
    assert M.simple_regret([1, 3, 3, 5], optimum=5) == [4, 2, 2, 0]
    assert M.simple_regret([6], optimum=5) == [0.0]  # never negative
