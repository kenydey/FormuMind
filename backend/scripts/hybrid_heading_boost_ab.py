#!/usr/bin/env python3
"""PageIndex 借鉴 A3: heading 粗召回 bonus A/B（kb_heading_boost）。

control: kb_heading_boost=0（现状）
treatment: kb_heading_boost=1 —— 融合前对 BM25/cosine 归一化分量做加性
           bonus（distinct heading_path 的 in-process BM25，weight=0.15）

指标：53 道 golden 题的 nDCG@6 / MRR / recall@6（与 run_golden_eval 同口径）。
翻转条件：ΔnDCG@6 >= +0.02（沿用 rerank A/B 的阈值惯例）且无显著退化；
inconclusive 则保持默认关闭。

沙箱 workaround：no_proxy 含括号 IPv6 会炸 httpx，启动时清洗。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

for key in ("no_proxy", "NO_PROXY"):
    raw = os.environ.get(key, "")
    if raw:
        os.environ[key] = ",".join(
            p for p in raw.split(",") if "[" not in p and "]" not in p
        )

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from app.services.kb_query_test import run_golden_eval  # noqa: E402


def _run(label: str, heading_boost: bool) -> dict:
    os.environ["FORMUMIND_KB_HEADING_BOOST"] = "1" if heading_boost else "0"
    # settings 单例在首次 get_settings 时固化 env —— 必须清缓存。
    from app import config

    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    t0 = time.perf_counter()
    out = run_golden_eval(mode="hybrid", top_k=6)
    out["elapsed_s"] = round(time.perf_counter() - t0, 1)
    print(
        f"[{label}] nDCG@6={out['ndcg_at_k']} MRR={out['mrr']} "
        f"recall@6={out['recall_at_k']} ({out['elapsed_s']}s)"
    )
    return out


def main() -> int:
    control = _run("control  ", False)
    treatment = _run("treatment", True)

    d_ndcg = round(treatment["ndcg_at_k"] - control["ndcg_at_k"], 4)
    d_mrr = round(treatment["mrr"] - control["mrr"], 4)
    d_recall = round(treatment["recall_at_k"] - control["recall_at_k"], 4)
    print(f"ΔnDCG@6={d_ndcg:+.4f}  ΔMRR={d_mrr:+.4f}  Δrecall@6={d_recall:+.4f}")

    if d_ndcg >= 0.02 and d_mrr >= -0.01:
        print("VERDICT: 开（增益达标，无显著退化）")
    elif d_ndcg <= -0.02:
        print("VERDICT: 关（显著退化）")
    else:
        print("VERDICT: 保持关闭（inconclusive）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
