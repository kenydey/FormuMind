#!/usr/bin/env python3
"""P2: hybrid 化学实体加成 A/B —— 实体聚焦版。

背景：53 道 golden 题 0 题含可提取实体，且存量 chunk 在回填前无 chem
元数据，第一轮 A/B 空转（Δ=0）。本轮：
  1. 先回填 chunk chem 元数据（scripts/backfill_chunk_entities.py）；
  2. 用实体题（CAS/分子式）直接测：relevance = chunk.meta.chem 含查询实体；
  3. control（无加成）vs treatment（kb_hybrid_entity_boost=1）比 MRR/recall@k。

翻转条件：ΔMRR ≥ +0.05（实体题集小，阈值高于通用集的 0.02）。
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
        os.environ[key] = ",".join(p for p in raw.split(",") if "[" not in p)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# （查询，期望实体值）—— 实体须在 KB chunk chem 元数据中真实存在。
ENTITY_QUESTIONS = [
    ("CAS 2530-83-8 硅烷偶联剂在金属表面处理中的作用", "2530-83-8"),
    ("2530-83-8 KH-560 改善涂层附着力的机理", "2530-83-8"),
    ("硅烷偶联剂 2530-83-8 的水解工艺", "2530-83-8"),
]


def _chunk_has_entity(chunk, entity: str) -> bool:
    meta = getattr(chunk, "meta", None) or {}
    values = {e.get("value") for e in (meta.get("chem") or [])}
    return entity in values


def _run(questions, entity_boost: bool, top_k: int = 10) -> dict:
    os.environ["FORMUMIND_KB_HYBRID_ENTITY_BOOST"] = "1" if entity_boost else "0"
    from app import config

    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    from app.services.hybrid_search import hybrid_search_scored

    mrrs: list[float] = []
    recalls: list[float] = []
    per_q: list[dict] = []
    for q, entity in questions:
        scored = hybrid_search_scored(q, top_k=top_k)
        # 相关 = chem 元数据含该实体
        rel_ranks = [
            i + 1 for i, sc in enumerate(scored) if _chunk_has_entity(sc.chunk, entity)
        ]
        # 全库相关数（recall 分母）
        mrr = 1.0 / rel_ranks[0] if rel_ranks else 0.0
        recall = len(rel_ranks) / max(1, len(rel_ranks))  # top-k 内命中率（简化）
        mrrs.append(mrr)
        recalls.append(recall)
        per_q.append({"q": q, "mrr": round(mrr, 4), "hits_in_topk": len(rel_ranks)})
    return {
        "mrr": round(sum(mrrs) / len(mrrs), 4) if mrrs else 0.0,
        "mean_hits_in_topk": round(sum(r["hits_in_topk"] for r in per_q) / len(per_q), 2),
        "per_q": per_q,
    }


def main() -> int:
    t0 = time.perf_counter()
    control = _run(ENTITY_QUESTIONS, False)
    treatment = _run(ENTITY_QUESTIONS, True)
    dt = round(time.perf_counter() - t0, 1)
    d_mrr = round(treatment["mrr"] - control["mrr"], 4)
    print(f"control   MRR={control['mrr']}  topk命中均值={control['mean_hits_in_topk']}")
    print(f"treatment MRR={treatment['mrr']}  topk命中均值={treatment['mean_hits_in_topk']}")
    print(f"ΔMRR={d_mrr:+.4f}  ({dt}s)")
    for c, t in zip(control["per_q"], treatment["per_q"]):
        flag = "↑" if t["mrr"] > c["mrr"] else ("↓" if t["mrr"] < c["mrr"] else "=")
        print(f"  {flag} {c['q'][:40]}: {c['mrr']} → {t['mrr']}")
    verdict = "ADOPT" if d_mrr >= 0.05 else "KEEP OFF"
    print(f"结论: {verdict}（翻转条件 ΔMRR≥+0.05）")
    out_path = Path(__file__).resolve().parent / "hybrid_entity_boost_ab_entity.json"
    out_path.write_text(
        json.dumps(
            {"control": control, "treatment": treatment,
             "delta_mrr": d_mrr, "verdict": verdict},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"结果已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
