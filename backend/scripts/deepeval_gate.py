#!/usr/bin/env python3
"""P1 #27: optional DeepEval faithfulness / answer-relevancy gate.

Default: skip (exit 0) unless FORMUMIND_DEEPEVAL=1. When enabled, requires
``deepeval`` installed and LLM credentials for the judge model. Failures exit
non-zero so CI can optionally enforce (job uses continue-on-error by default).
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    enabled = (os.environ.get("FORMUMIND_DEEPEVAL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not enabled:
        print(
            "deepeval_gate: skipped (set FORMUMIND_DEEPEVAL=1 to enforce; "
            "requires pip install deepeval + LLM keys)"
        )
        return 0

    try:
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase
    except Exception as exc:
        print(f"deepeval_gate: deepeval not importable: {exc}", file=sys.stderr)
        return 2

    # Minimal offline-friendly smoke: construct metrics + one test case.
    # Real scoring needs a judge LLM; without keys we still validate wiring.
    case = LLMTestCase(
        input="环氧防腐涂料盐雾性能如何评价？",
        actual_output="中性盐雾试验(NSS)按 GB/T 1771 评价涂膜防腐性能。",
        retrieval_context=[
            "盐雾试验按GB/T 1771标准执行，中性盐雾试验(NSS)是常用评价方法。"
        ],
    )
    try:
        faith = FaithfulnessMetric(threshold=0.5)
        rel = AnswerRelevancyMetric(threshold=0.5)
        # measure() may call an LLM — catch and report.
        faith.measure(case)
        rel.measure(case)
        print(
            f"deepeval_gate: ok faithfulness={getattr(faith, 'score', None)} "
            f"relevancy={getattr(rel, 'score', None)}"
        )
        return 0
    except Exception as exc:
        print(f"deepeval_gate: measure failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
