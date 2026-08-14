"""Turn benchmark results.json into a tiered comparison report.

Reads results.json (summary stats per model) + ground_truth.json, then prints:
  * overall table: exact / parseable / formula / mean-tanimoto per model
  * per-tier breakdown (tier 1 simple / tier 2 medium / tier 3 hard)
  * a one-line recommendation (best model per tier).

Tier is derived from the ground-truth record order (first 5 = tier 1,
next 8 = tier 2, rest = tier 3), matching generate_testset.py's layout.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

TIER_BOUNDS = {"tier1": range(0, 5), "tier2": range(5, 13), "tier3": range(13, 20)}


def main() -> None:
    with open(os.path.join(HERE, "ground_truth.json"), encoding="utf-8") as f:
        gt = json.load(f)
    with open(os.path.join(HERE, "results.json"), encoding="utf-8") as f:
        results = json.load(f)

    print("\n" + "=" * 70)
    print("模型总体对比")
    print("=" * 70)
    print(f"  {'模型':<16} {'完全匹配':<12} {'可解析':<10} {'分子式':<10} {'Tanimoto':<10}")
    for model, s in results.items():
        print(
            f"  {s['model']:<16} {s['exact_rate']*100:>4.0f}% ({s['exact']:>2}/{s['n']})  "
            f"{s['parseable_rate']*100:>4.0f}%      {s['formula_rate']*100:>4.0f}%    "
            f"{s['mean_tanimoto']:.2f}"
        )

    # Reuse per-image rows if present (benchmark.py may store them), otherwise
    # fall back to summary-only and skip the tier table.
    for model, s in results.items():
        if "per_image" not in s:
            print("\n(未找到逐图结果，跳过难度分层——重跑 benchmark.py 并存储 per_image 即可)")
            return

    print("\n" + "=" * 70)
    print("按难度分层")
    print("=" * 70)
    for tier, idxs in TIER_BOUNDS.items():
        print(f"\n  [{tier}]")
        for model, s in results.items():
            rows = [s["per_image"][i] for i in idxs]
            exact = sum(1 for r in rows if r["exact"])
            print(
                f"    {s['model']:<16} 完全匹配 {exact}/{len(rows)} "
                f"({exact/len(rows)*100:.0f}%)"
            )


if __name__ == "__main__":
    main()
