#!/usr/bin/env python3
"""C-6 rerank A/B one-shot runner (run when real embeddings are available).

Usage:
    cd backend && .venv/bin/python scripts/run_rerank_ab.py [--top-k 6]

Runs hybrid vs hybrid_rerank on the golden set, prints the comparison
report and the default-switch decision. Requires:
  - document_chunks with real embeddings (else hybrid_rerank degrades to
    keyword order and the comparison is meaningless);
  - cross-encoder model reachable (rerank_plugin, fail-open otherwise).

Exit 0 always; the DECISION is in the JSON output. Do not flip
``search_rerank_enabled`` default without Cheng's explicit approval.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import argparse  # noqa: E402

from app.services.rerank_ab import (  # noqa: E402
    rerank_default_decision,
    run_rerank_ab,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="C-6 rerank A/B (needs real embeddings)")
    ap.add_argument("--top-k", type=int, default=6)
    ap.add_argument("--project-id", default=None)
    args = ap.parse_args()

    from app.db.chunk_store import get_chunk_store

    fp = get_chunk_store().embedded_fingerprint()
    total_embedded = sum(int(v.get("count", 0)) for v in fp.values())
    if total_embedded == 0:
        print(
            json.dumps(
                {
                    "error": "no embeddings in document_chunks — "
                    "real A/B blocked, run embedding backfill first",
                    "decision": {"enable_by_default": False},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    report = run_rerank_ab(top_k=args.top_k, project_id=args.project_id)
    decision = rerank_default_decision(report)
    print(
        json.dumps(
            {"ab_report": report, "decision": decision},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
