"""C-6: cross-encoder rerank A/B evaluation framework.

Compares ``hybrid`` (control) vs ``hybrid_rerank`` (treatment) on the golden
question set using nDCG@k (primary), recall@k and MRR (secondary), and turns
the result into a default-switch decision.

Decision rule (C-6): enable ``search_rerank_enabled`` by default only when
mean ΔnDCG@k > +0.02 on REAL corpus + REAL embeddings. A smaller (or
negative) delta keeps the default OFF (fail-safe: rerank costs latency and
a cross-encoder model).

Status honesty (2026-10-01): this framework's MECHANICS are unit-tested with
synthetic run_query_test stubs (explicitly labelled). The REAL A/B on the
chemistry corpus is BLOCKED — dev DB has 0 embeddings and the sandbox
cannot download embedding / cross-encoder models (proxy-blocked HF).
Do NOT feed synthetic numbers into rerank_default_decision and claim a
corpus conclusion.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Minimum mean nDCG@k gain (treatment − control) required to flip the
#: rerank default ON. Guards against noise-driven flips on small golden sets.
RERANK_NDCG_FLIP_THRESHOLD = 0.02


def run_rerank_ab(
    *,
    top_k: int = 6,
    project_id: str | None = None,
    include_global: bool = True,
) -> dict[str, Any]:
    """Run control vs treatment golden evals and return a comparison report.

    Both arms run the same questions; only the rerank stage differs.
    """
    from .kb_query_test import run_golden_eval

    control = run_golden_eval(
        mode="hybrid", top_k=top_k, project_id=project_id,
        include_global=include_global,
    )
    treatment = run_golden_eval(
        mode="hybrid_rerank", top_k=top_k, project_id=project_id,
        include_global=include_global, rerank=True,
    )
    d_ndcg = round(treatment["ndcg_at_k"] - control["ndcg_at_k"], 4)
    d_recall = round(treatment["recall_at_k"] - control["recall_at_k"], 4)
    d_mrr = round(treatment["mrr"] - control["mrr"], 4)
    return {
        "top_k": top_k,
        "control": {
            "mode": "hybrid",
            "ndcg_at_k": control["ndcg_at_k"],
            "recall_at_k": control["recall_at_k"],
            "mrr": control["mrr"],
            "total": control["total"],
        },
        "treatment": {
            "mode": "hybrid_rerank",
            "ndcg_at_k": treatment["ndcg_at_k"],
            "recall_at_k": treatment["recall_at_k"],
            "mrr": treatment["mrr"],
            "total": treatment["total"],
        },
        "delta_ndcg_at_k": d_ndcg,
        "delta_recall_at_k": d_recall,
        "delta_mrr": d_mrr,
    }


def rerank_default_decision(ab_report: dict[str, Any]) -> dict[str, Any]:
    """Turn an A/B report into a default-switch recommendation.

    Returns {"enable_by_default": bool, "reason": str}. The threshold is
    RERANK_NDCG_FLIP_THRESHOLD; ties/negatives keep the default OFF.
    """
    d = float(ab_report.get("delta_ndcg_at_k") or 0.0)
    if d > RERANK_NDCG_FLIP_THRESHOLD:
        return {
            "enable_by_default": True,
            "delta_ndcg_at_k": d,
            "reason": (
                f"mean ΔnDCG@k = {d:+.4f} > +{RERANK_NDCG_FLIP_THRESHOLD} "
                "on the golden set: rerank default ON"
            ),
        }
    return {
        "enable_by_default": False,
        "delta_ndcg_at_k": d,
        "reason": (
            f"mean ΔnDCG@k = {d:+.4f} ≤ +{RERANK_NDCG_FLIP_THRESHOLD}: "
            "rerank stays default OFF (opt-in)"
        ),
    }
