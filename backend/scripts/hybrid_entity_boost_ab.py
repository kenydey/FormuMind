#!/usr/bin/env python3
"""P2: hybrid 化学实体加成 A/B（kb_hybrid_entity_boost）。

control: 融合前无实体加成（现状）
treatment: kb_hybrid_entity_boost=1 —— 融合前对 BM25/cosine 归一化分量
           做乘性 (1+boost)，boost 来自 legacy _entity_boost（CAS/分子式/
           牌号/SMILES，∈[0,0.6]）

指标：53 道 golden 题的 nDCG@6 / MRR / recall@6（与 run_golden_eval 同口径）。
翻转条件：ΔnDCG@6 ≥ +0.02（沿用 rerank A/B 的阈值惯例）且无显著退化。

沙箱 workaround：no_proxy 含括号 IPv6 会炸 httpx，启动时清洗。
"""
from __future__ import annotations

import json
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


def _run(label: str, entity_boost: bool) -> dict:
    os.environ["FORMUMIND_KB_HYBRID_ENTITY_BOOST"] = "1" if entity_boost else "0"
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
    control = _run("control ", False)
    treatment = _run("treatment", True)

    d_ndcg = round(treatment["ndcg_at_k"] - control["ndcg_at_k"], 4)
    d_mrr = round(treatment["mrr"] - control["mrr"], 4)
    d_recall = round(treatment["recall_at_k"] - control["recall_at_k"], 4)
    print(f"ΔnDCG@6={d_ndcg:+.4f}  ΔMRR={d_mrr:+.4f}  Δrecall@6={d_recall:+.4f}")

    # 逐题翻转：哪些题因加成变好/变差
    c_res = {r["question"]: r["ndcg_at_k"] for r in control["results"]}
    t_res = {r["question"]: r["ndcg_at_k"] for r in treatment["results"]}
    improved = [(q, t_res[q] - c_res[q]) for q in c_res if t_res[q] > c_res[q]]
    regressed = [(q, t_res[q] - c_res[q]) for q in c_res if t_res[q] < c_res[q]]
    improved.sort(key=lambda x: -x[1])
    regressed.sort(key=lambda x: x[1])
    print(f"变好 {len(improved)} 题 / 变差 {len(regressed)} 题")
    for q, d in improved[:5]:
        print(f"  +{d:.3f} {q[:60]}")
    for q, d in regressed[:5]:
        print(f"  {d:.3f} {q[:60]}")

    verdict = "ADOPT" if d_ndcg >= 0.02 and d_mrr >= -0.005 else "KEEP OFF"
    print(f"结论: {verdict}（翻转条件 ΔnDCG@6≥+0.02 且 MRR 不显著退化）")
    out_path = Path(__file__).resolve().parent / "hybrid_entity_boost_ab.json"
    out_path.write_text(
        json.dumps(
            {
                "control": {k: control[k] for k in ("ndcg_at_k", "mrr", "recall_at_k")},
                "treatment": {k: treatment[k] for k in ("ndcg_at_k", "mrr", "recall_at_k")},
                "delta_ndcg": d_ndcg,
                "delta_mrr": d_mrr,
                "delta_recall": d_recall,
                "improved": len(improved),
                "regressed": len(regressed),
                "verdict": verdict,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
